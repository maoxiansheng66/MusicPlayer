import sys
import os
import sqlite3
import datetime
import platform
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
                             QSystemTrayIcon, QMenu, QAction, QTimeEdit,
                             QLineEdit, QFileDialog, QMessageBox, QLabel, QCheckBox,
                             QSlider, QGroupBox, QGridLayout, QListWidget,
                             QTextBrowser, QStackedWidget, QFrame, QFormLayout)
from PyQt5.QtCore import Qt, QTime, pyqtSignal, QObject, QSettings
from apscheduler.schedulers.background import BackgroundScheduler
import pygame.mixer


# ==========================
# 1. 数据库管理 (优化连接管理)
# ==========================
class DBManager:
    def __init__(self):
        app_data_path = os.path.join(os.path.expanduser('~'), '.MusicScheduler')
        if not os.path.exists(app_data_path):
            os.makedirs(app_data_path)
        self.db_name = os.path.join(app_data_path, 'scheduler_data.db')
        self.init_db()

    def get_conn(self):
        return sqlite3.connect(self.db_name)

    def init_db(self):
        with self.get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS tasks(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT,
                    play_time TEXT,
                    week_days TEXT,
                    file_path TEXT,
                    volume INTEGER DEFAULT 100,
                    is_enabled INTEGER DEFAULT 1
                )
            ''')
            conn.commit()

    def add_task(self, name, play_time, week_days, file_path, volume):
        with self.get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO tasks (name, play_time, week_days, file_path, volume, is_enabled) VALUES (?, ?, ?, ?, ?, 1)",
                (name, play_time, week_days, file_path, volume))
            conn.commit()
            return cursor.lastrowid

    def update_task(self, task_id, name, play_time, week_days, file_path, volume):
        with self.get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE tasks SET name=?, play_time=?, week_days=?, file_path=?, volume=? WHERE id=?",
                           (name, play_time, week_days, file_path, volume, task_id))
            conn.commit()

    def get_all_tasks(self):
        with self.get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, name, play_time, week_days, file_path, volume FROM tasks")
            return cursor.fetchall()

    def delete_task(self, task_id):
        with self.get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM tasks WHERE id=?", (task_id,))
            conn.commit()


# ==========================
# 2. 音频引擎 (改进异常处理)
# ==========================
class AudioPlayer:
    def __init__(self):
        try:
            pygame.mixer.pre_init(frequency=44100, size=-16, channels=2, buffer=512)
            pygame.mixer.init()
        except Exception as e:
            print(f"音频系统初始化失败：{e}")

    def play(self, file_path, volume=100):
        try:
            self.stop()
            if not os.path.exists(file_path):
                return False, f"文件不存在：{file_path}"
            pygame.mixer.music.load(file_path)
            vol_float = max(0.0, min(volume / 100.0, 2.0))
            pygame.mixer.music.set_volume(vol_float)
            pygame.mixer.music.play()
            return True, "播放成功"
        except pygame.error as e:
            return False, f"播放失败 (格式不支持): {str(e)}"
        except Exception as e:
            return False, f"未知错误：{str(e)}"

    def stop(self):
        try:
            pygame.mixer.music.stop()
            pygame.mixer.music.unload()
        except Exception:
            pass


# ==========================
# 3. 全局信号
# ==========================
class Communicate(QObject):
    log_signal = pyqtSignal(str)
    play_signal = pyqtSignal(int)


# ==========================
# 4. 主界面
# ==========================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.db = DBManager()
        self.player = AudioPlayer()
        self.scheduler = BackgroundScheduler()
        self.comm = Communicate(self)
        self.settings = QSettings("MySoft", "MusicScheduler")
        self.current_edit_id = None
        self.current_global_volume = self.settings.value("global_volume", 100, type=int)
        self.comm.log_signal.connect(self.append_log)
        self.comm.play_signal.connect(self.execute_task)
        try:
            self.scheduler.start()
        except Exception as e:
            self.append_log(f"调度器启动失败：{e}")
        self.init_ui()
        self.init_tray()
        self.load_playlist()
        self.load_tasks_to_table()
        self.reload_scheduler_jobs()
        self.append_log("系统启动成功。")

    def init_ui(self):
        self.setWindowTitle("全平台定时音乐播放系统 (修复版)")
        self.resize(1000, 700)
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        sidebar = QFrame()
        sidebar.setFixedWidth(180)
        sidebar.setStyleSheet("background-color: #f3f3f3; border-right: 1px solid #ddd;")
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        title_label = QLabel("音乐播控")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("font-size: 20px; font-weight: bold; padding: 20px; color: #0078d7;")
        sidebar_layout.addWidget(title_label)
        self.btn_play_now = QPushButton("▶ 立即播放")
        self.btn_timer = QPushButton("⏰ 定时播放")
        self.btn_log = QPushButton("📜 播放日志")
        self.btn_settings = QPushButton("⚙ 设置")
        for btn in [self.btn_play_now, self.btn_timer, self.btn_log, self.btn_settings]:
            btn.setCheckable(True)
            btn.setAutoExclusive(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setMinimumHeight(40)
            btn.setStyleSheet("""
                QPushButton { background-color: transparent; text-align: left; padding-left: 20px; font-size: 14px; border: none; color: #333; }
                QPushButton:hover { background-color: #e0e0e0; }
                QPushButton:checked { background-color: #0078d7; color: white; font-weight: bold; }
            """)
            sidebar_layout.addWidget(btn)
        self.btn_play_now.setChecked(True)
        sidebar_layout.addStretch()
        main_layout.addWidget(sidebar)
        self.stacked_widget = QStackedWidget()
        main_layout.addWidget(self.stacked_widget)
        page_play = QWidget()
        layout_play = QVBoxLayout(page_play)
        pl_tools = QHBoxLayout()
        btn_add_files = QPushButton("添加文件")
        btn_add_files.clicked.connect(self.add_files_to_playlist)
        btn_scan_folder = QPushButton("扫描文件夹")
        btn_scan_folder.clicked.connect(self.scan_folder_to_playlist)
        btn_clear_list = QPushButton("清空列表")
        btn_clear_list.clicked.connect(self.clear_playlist)
        pl_tools.addWidget(btn_add_files)
        pl_tools.addWidget(btn_scan_folder)
        pl_tools.addWidget(btn_clear_list)
        layout_play.addLayout(pl_tools)
        self.play_list_widget = QListWidget()
        self.play_list_widget.setStyleSheet("font-size: 14px;")
        self.play_list_widget.doubleClicked.connect(self.play_instant_item)
        layout_play.addWidget(QLabel("播放列表 (双击播放，退出自动保存):"))
        layout_play.addWidget(self.play_list_widget)
        ctrl_bar = QHBoxLayout()
        self.btn_play_sel = QPushButton("▶ 播放选中")
        self.btn_play_sel.setStyleSheet("background-color: #4CAF50; color: white; padding: 10px;")
        self.btn_play_sel.clicked.connect(self.play_instant_item)
        self.btn_stop = QPushButton("■ 停止")
        self.btn_stop.setStyleSheet("background-color: #f44336; color: white; padding: 10px;")
        self.btn_stop.clicked.connect(self.stop_audio)
        ctrl_bar.addWidget(self.btn_play_sel)
        ctrl_bar.addWidget(self.btn_stop)
        layout_play.addLayout(ctrl_bar)
        self.stacked_widget.addWidget(page_play)
        page_timer = QWidget()
        layout_timer = QVBoxLayout(page_timer)
        self.task_table = QTableWidget()
        self.task_table.setColumnCount(4)
        self.task_table.setHorizontalHeaderLabels(["时间", "星期", "文件", "音量%"])
        self.task_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.task_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.task_table.customContextMenuRequested.connect(self.on_task_right_click)
        layout_timer.addWidget(self.task_table)
        form_group = QGroupBox("任务编辑")
        form_layout = QGridLayout()
        form_layout.addWidget(QLabel("播放时间:"), 0, 0)
        self.time_edit = QTimeEdit()
        self.time_edit.setDisplayFormat("HH:mm:ss")
        self.time_edit.setTime(QTime.currentTime())
        form_layout.addWidget(self.time_edit, 0, 1)
        form_layout.addWidget(QLabel("星期:"), 0, 2)
        week_widget = QWidget()
        week_layout = QHBoxLayout(week_widget)
        week_layout.setContentsMargins(0, 0, 0, 0)
        self.weekdays_cb = []
        for d in ["一", "二", "三", "四", "五", "六", "日"]:
            cb = QCheckBox(d)
            cb.setChecked(True)
            self.weekdays_cb.append(cb)
            week_layout.addWidget(cb)
        form_layout.addWidget(week_widget, 0, 3, 1, 2)
        form_layout.addWidget(QLabel("文件:"), 1, 0)
        self.task_file_edit = QLineEdit()
        form_layout.addWidget(self.task_file_edit, 1, 1, 1, 3)
        btn_sel_task_file = QPushButton("选择")
        btn_sel_task_file.clicked.connect(self.select_task_file)
        form_layout.addWidget(btn_sel_task_file, 1, 4)
        form_layout.addWidget(QLabel("音量:"), 2, 0)
        self.task_vol_slider = QSlider(Qt.Horizontal)
        self.task_vol_slider.setRange(0, 200)
        self.task_vol_slider.setValue(100)
        self.task_vol_label = QLabel("100%")
        self.task_vol_slider.valueChanged.connect(lambda v: self.task_vol_label.setText(f"{v}%"))
        form_layout.addWidget(self.task_vol_slider, 2, 1, 1, 2)
        form_layout.addWidget(self.task_vol_label, 2, 3)
        self.btn_add_task = QPushButton("添加任务")
        self.btn_add_task.setStyleSheet("background-color: #0078d7; color: white;")
        self.btn_add_task.clicked.connect(self.save_task)
        form_layout.addWidget(self.btn_add_task, 2, 4)
        form_group.setLayout(form_layout)
        layout_timer.addWidget(form_group)
        self.stacked_widget.addWidget(page_timer)
        page_log = QWidget()
        layout_log = QVBoxLayout(page_log)
        self.log_browser = QTextBrowser()
        self.log_browser.setStyleSheet("font-size: 13px;")
        layout_log.addWidget(self.log_browser)
        self.stacked_widget.addWidget(page_log)
        page_settings = QWidget()
        layout_settings = QVBoxLayout(page_settings)
        audio_group = QGroupBox("音频设置")
        audio_layout = QFormLayout()
        self.global_vol_slider = QSlider(Qt.Horizontal)
        self.global_vol_slider.setRange(0, 200)
        self.global_vol_slider.setValue(self.current_global_volume)
        self.global_vol_label = QLabel(f"{self.current_global_volume}%")
        self.global_vol_slider.valueChanged.connect(self.save_global_volume)
        vol_layout = QHBoxLayout()
        vol_layout.addWidget(self.global_vol_slider)
        vol_layout.addWidget(self.global_vol_label)
        audio_layout.addRow("软件音量上限:", vol_layout)
        audio_layout.addRow(QLabel("提示：100%为原音，超过 100% 为增益。"))
        audio_group.setLayout(audio_layout)
        layout_settings.addWidget(audio_group)
        sys_group = QGroupBox("系统设置")
        sys_layout = QFormLayout()
        self.auto_start_cb = QCheckBox("开机自动启动")
        if self.settings.value("auto_start", "false") == "true":
            self.auto_start_cb.setChecked(True)
        self.auto_start_cb.stateChanged.connect(self.toggle_auto_start)
        sys_layout.addRow(self.auto_start_cb)
        sys_group.setLayout(sys_layout)
        layout_settings.addWidget(sys_group)
        layout_settings.addStretch()
        self.stacked_widget.addWidget(page_settings)
        self.btn_play_now.clicked.connect(lambda: self.stacked_widget.setCurrentIndex(0))
        self.btn_timer.clicked.connect(lambda: self.stacked_widget.setCurrentIndex(1))
        self.btn_log.clicked.connect(lambda: self.stacked_widget.setCurrentIndex(2))
        self.btn_settings.clicked.connect(lambda: self.stacked_widget.setCurrentIndex(3))
        self.statusBar().showMessage("准备就绪")

    def append_log(self, text):
        timestamp = datetime.datetime.now().strftime("[%H:%M:%S]")
        self.log_browser.append(f"{timestamp} {text}")

    def save_global_volume(self, val):
        self.settings.setValue("global_volume", val)
        self.current_global_volume = val
        self.global_vol_label.setText(f"{val}%")

    def toggle_auto_start(self, state):
        is_checked = (state == Qt.Checked)
        self.settings.setValue("auto_start", str(is_checked).lower())

        system = platform.system()

        if system == "Windows":
            try:
                import winreg
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                   r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                                   winreg.KEY_WRITE)
                path = sys.executable if getattr(sys, 'frozen', False) else f'"{sys.executable}" "{__file__}"'
                if is_checked:
                    winreg.SetValueEx(key, "MusicScheduler", 0, winreg.REG_SZ, path)
                else:
                    try:
                        winreg.DeleteValue(key, "MusicScheduler")
                    except FileNotFoundError:
                        pass
                winreg.CloseKey(key)
            except Exception as e:
                self.append_log(f"设置自启失败：{e}")

        elif system == "Darwin":
            try:
                plist_path = os.path.expanduser("~/Library/LaunchAgents/com.MusicScheduler.plist")
                if is_checked:
                    script_path = os.path.abspath(__file__)
                    plist_content = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.MusicScheduler</string>
    <key>ProgramArguments</key>
    <array>
        <string>{sys.executable}</string>
        <string>{script_path}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/MusicScheduler.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/MusicScheduler.err</string>
</dict>
</plist>'''
                    with open(plist_path, 'w') as f:
                        f.write(plist_content)
                    os.chmod(plist_path, 0o644)
                    self.append_log("macOS 开机自启已启用")
                else:
                    if os.path.exists(plist_path):
                        os.remove(plist_path)
                        self.append_log("macOS 开机自启已禁用")
            except Exception as e:
                self.append_log(f"设置 macOS 自启失败：{e}")

    def save_playlist(self):
        items = []
        for i in range(self.play_list_widget.count()):
            items.append(self.play_list_widget.item(i).text())
        self.settings.setValue("playlist", items)
        self.append_log(f"播放列表已保存 ({len(items)} 首音乐)")

    def load_playlist(self):
        items = self.settings.value("playlist", [])
        if items:
            valid_count = 0
            for item in items:
                if os.path.exists(item):
                    self.play_list_widget.addItem(item)
                    valid_count += 1
                else:
                    self.append_log(f"警告：文件不存在 - {os.path.basename(item)}")
            self.append_log(f"已恢复 {valid_count} 个有效的播放列表项（共 {len(items)} 个）")
        else:
            self.append_log("播放列表为空")

    def clear_playlist(self):
        self.play_list_widget.clear()
        self.settings.remove("playlist")
        self.append_log("播放列表已清空")

    def load_playlist(self):
        items = self.settings.value("playlist", [])
        if items:
            for item in items:
                self.play_list_widget.addItem(item)
            self.append_log(f"已恢复 {len(items)} 个播放列表项。")

    def clear_playlist(self):
        self.play_list_widget.clear()
        self.settings.remove("playlist")

    def add_files_to_playlist(self):
        f_filter = "音频文件 (*.mp3 *.wav *.ogg *.flac *.m4a);;所有文件 (*.*)"
        files, _ = QFileDialog.getOpenFileNames(self, "选择文件", "", f_filter)
        if files:
            for f in files:
                self.play_list_widget.addItem(f)
            self.append_log(f"添加了 {len(files)} 个文件。")
            self.save_playlist()

    def scan_folder_to_playlist(self):
        folder = QFileDialog.getExistingDirectory(self, "选择音乐文件夹")
        if folder:
            supported_ext = ('.mp3', '.wav', '.ogg', '.flac', '.m4a')
            count = 0
            for root, dirs, files in os.walk(folder):
                for file in files:
                    if file.lower().endswith(supported_ext):
                        self.play_list_widget.addItem(os.path.join(root, file))
                        count += 1
            self.append_log(f"扫描添加了 {count} 首音乐。")
            self.save_playlist()

    def play_instant_item(self):
        item = self.play_list_widget.currentItem()
        if item:
            path = item.text()
            vol = self.current_global_volume
            success, msg = self.player.play(path, vol)
            if success:
                self.append_log(f"正在播放：{os.path.basename(path)}")
            else:
                self.append_log(f"播放失败：{msg}")

    def stop_audio(self):
        self.player.stop()
        self.append_log("已停止播放。")

    def select_task_file(self):
        f_filter = "音频文件 (*.mp3 *.wav *.ogg *.flac *.m4a);;所有文件 (*.*)"
        f, _ = QFileDialog.getOpenFileName(self, "选择文件", "", f_filter)
        if f: self.task_file_edit.setText(f)

    def on_task_right_click(self, pos):
        item = self.task_table.itemAt(pos)
        if item:
            row = item.row()
            tasks = self.db.get_all_tasks()
            if row < len(tasks):
                task_id = tasks[row][0]
                menu = QMenu()
                edit_action = QAction("编辑任务", self)
                edit_action.triggered.connect(lambda: self.load_task_to_edit(task_id))
                menu.addAction(edit_action)
                del_action = QAction("删除任务", self)
                del_action.triggered.connect(lambda: self.delete_task(task_id))
                menu.addAction(del_action)
                menu.exec_(self.task_table.mapToGlobal(pos))

    def load_task_to_edit(self, task_id):
        tasks = self.db.get_all_tasks()
        task = [t for t in tasks if t[0] == task_id]
        if task:
            t = task[0]
            h, m, s = map(int, t[2].split(':'))
            self.time_edit.setTime(QTime(h, m, s))
            days = t[3].split(',') if t[3] else []
            days_map = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
            for i, cb in enumerate(self.weekdays_cb):
                cb.setChecked(days_map[i] in days)
            self.task_file_edit.setText(t[4])
            self.task_vol_slider.setValue(t[5])
            self.current_edit_id = task_id
            self.btn_add_task.setText("保存修改")
            self.btn_add_task.setStyleSheet("background-color: #FF9800; color: white;")
            self.append_log(f"正在编辑任务 ID: {task_id}")

    def save_task(self):
        path = self.task_file_edit.text()
        if not path:
            QMessageBox.warning(self, "错误", "请选择音频文件")
            return
        name = os.path.basename(path)
        time_str = self.time_edit.time().toString("HH:mm:ss")
        week_str = self.get_week_str()
        volume = self.task_vol_slider.value()
        if self.current_edit_id:
            self.db.update_task(self.current_edit_id, name, time_str, week_str, path, volume)
            self.append_log(f"任务已更新：{time_str}")
            self.current_edit_id = None
            self.btn_add_task.setText("添加任务")
            self.btn_add_task.setStyleSheet("background-color: #0078d7; color: white;")
        else:
            self.db.add_task(name, time_str, week_str, path, volume)
            self.append_log(f"任务已添加：{time_str}")
        self.load_tasks_to_table()
        self.reload_scheduler_jobs()

    def delete_task(self, task_id):
        self.db.delete_task(task_id)
        self.load_tasks_to_table()
        self.reload_scheduler_jobs()
        self.append_log(f"删除了任务 ID: {task_id}")

    def get_week_str(self):
        days_map = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        selected = [days_map[i] for i, cb in enumerate(self.weekdays_cb) if cb.isChecked()]
        return ",".join(selected)

    def convert_week_to_chinese(self, week_str):
        if not week_str: return "未设置"
        if week_str == "mon,tue,wed,thu,fri,sat,sun": return "每天"
        if week_str == "mon,tue,wed,thu,fri": return "工作日"
        map_dict = {"mon": "一", "tue": "二", "wed": "三", "thu": "四", "fri": "五", "sat": "六", "sun": "日"}
        parts = week_str.split(',')
        cn_parts = [map_dict.get(p, p) for p in parts]
        return "周" + ",".join(cn_parts)

    def load_tasks_to_table(self):
        self.task_table.setRowCount(0)
        tasks = self.db.get_all_tasks()
        for t in tasks:
            row = self.task_table.rowCount()
            self.task_table.insertRow(row)
            self.task_table.setItem(row, 0, QTableWidgetItem(t[2]))
            wd_cn = self.convert_week_to_chinese(t[3])
            self.task_table.setItem(row, 1, QTableWidgetItem(wd_cn))
            file_item = QTableWidgetItem(os.path.basename(t[4]))
            file_item.setToolTip(t[4])
            self.task_table.setItem(row, 2, file_item)
            self.task_table.setItem(row, 3, QTableWidgetItem(f"{t[5]}%"))

    def init_tray(self):
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.activated.connect(self.on_tray_activated)
        tray_menu = QMenu()
        show_action = QAction("显示主界面", self)
        show_action.triggered.connect(self.show)
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.quit_app)
        tray_menu.addAction(show_action)
        tray_menu.addAction(quit_action)
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.show()

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.showNormal()
            self.activateWindow()

    def closeEvent(self, event):
        self.save_playlist()
        event.ignore()
        self.hide()
        self.tray_icon.showMessage("后台运行", "程序已最小化到系统托盘", QSystemTrayIcon.Information, 2000)

    def quit_app(self):
        self.scheduler.shutdown()
        self.save_playlist()
        QApplication.quit()

    def reload_scheduler_jobs(self):
        self.scheduler.remove_all_jobs()
        tasks = self.db.get_all_tasks()
        for t in tasks:
            task_id, name, play_time, week_days, file_path, volume = t[:6]
            if not week_days or '-' in week_days:
                continue
            h, m, s = map(int, play_time.split(':'))
            try:
                self.scheduler.add_job(
                    lambda tid=task_id: self.comm.play_signal.emit(tid),
                    'cron',
                    day_of_week=week_days,
                    hour=h,
                    minute=m,
                    second=s
                )
            except Exception as e:
                self.append_log(f"任务调度失败：{name} - {str(e)}")

    def execute_task(self, task_id):
        tasks = self.db.get_all_tasks()
        task = [t for t in tasks if t[0] == task_id]
        if task:
            t = task[0]
            name, path, vol = t[1], t[4], t[5]
            self.append_log(f"定时任务触发：{name}")
            final_vol = int(vol * self.current_global_volume / 100)
            success, msg = self.player.play(path, final_vol)
            if not success:
                self.append_log(f"任务播放失败：{msg}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
