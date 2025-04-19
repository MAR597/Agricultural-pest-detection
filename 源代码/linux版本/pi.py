import cv2
import tkinter as tk
from tkinter import ttk, Button, Label, scrolledtext, Spinbox
from tkinter.font import Font
from PIL import Image, ImageTk
import threading
import queue
import time
import serial
import serial.tools.list_ports
import re
import base64
import requests
from concurrent.futures import ThreadPoolExecutor

# 全局队列用于存储子进程的输出
output_queue = queue.Queue()
# 用于线程间通信的队列
message_queue = queue.Queue()
# 用于保护串口资源的锁
serial_lock = threading.Lock()
# 串口对象
serial_port = None
# 拍照文件路径
photo_path = "captured_photo.jpg"
# API 密钥
API_KEY = "A5je7eqj6hVh8clcwetjiiQr"
SECRET_KEY = "mj9ppYQu1AF0D1XH9DHcrWI8WJXSNDN1"

# 初始化VideoCapture对象，使用 USB 摄像头
cap = cv2.VideoCapture(0)

# 检查视频是否成功打开
if not cap.isOpened():
    print("Error: Could not open video.")
    exit()

# 线程池
executor = ThreadPoolExecutor(max_workers=5)


# 自动检测并打开串口的函数
def auto_open_serial(text_widget):
    global serial_port
    ports = serial.tools.list_ports.comports()
    for port in ports:
        try:
            with serial_lock:
                serial_port = serial.Serial(port.device, baudrate=9600, timeout=0.1)
            message_queue.put((text_widget, f"串口 {serial_port.portstr} 打开成功"))
            # 启动串口读取线程
            threading.Thread(target=read_serial_data, args=(text_widget,), daemon=True).start()
            return True
        except serial.SerialException as e:
            message_queue.put((text_widget, f"尝试打开串口 {port.device} 失败: {e}"))
            continue
    message_queue.put((text_widget, "未找到可用的串口"))
    serial_port = None
    return False


# 关闭串口的函数
def close_serial(text_widget):
    global serial_port
    with serial_lock:
        if serial_port and serial_port.is_open:
            try:
                serial_port.close()
                message_queue.put((text_widget, f"串口 {serial_port.portstr} 已关闭"))
            except Exception as e:
                message_queue.put((text_widget, f"关闭串口时出错: {e}"))
            serial_port = None


# 日志记录函数，确保在主线程更新文本框
def log_message(text_widget, message):
    root.after(0, lambda: text_widget.insert(tk.END, message + "\n"))
    root.after(0, lambda: text_widget.see(tk.END))


# 处理消息队列中的消息
def process_message_queue():
    while not message_queue.empty():
        text_widget, message = message_queue.get()
        log_message(text_widget, message)
    root.after(100, process_message_queue)


# 串口发送字符串的函数，并打印发送的数据
def send_serial_command(command, text_widget):
    def send_and_log():
        with serial_lock:
            try:
                if serial_port and serial_port.is_open:
                    serial_port.write(command.encode())
                    message_queue.put((text_widget, f"Sent command to serial: {command}"))
                else:
                    if auto_open_serial(text_widget):
                        serial_port.write(command.encode())
                        message_queue.put((text_widget, f"Sent command to serial: {command}"))
                    else:
                        message_queue.put((text_widget, f"串口未成功打开，无法发送命令：{command}"))
            except Exception as e:
                message_queue.put((text_widget, f"串口发送错误: {e}"))

    executor.submit(send_and_log)


# 发送 p 后 5 秒发送 q，并关闭串口
def zdjs(text_widget):
    send_serial_command('p', text_widget)
    time.sleep(0.5)
    send_serial_command('Y', text_widget)
    time.sleep(5)
    send_serial_command('Z', text_widget)
    close_serial(text_widget)
    message_queue.put((text_widget, "串口已关闭"))


# 更新Text组件的函数
def update_text(text_widget):
    try:
        while True:
            line = output_queue.get_nowait()
            log_message(text_widget, line)
    except queue.Empty:
        root.after(50, update_text, text_widget)


# 获取access token
def get_access_token(text_widget):
    url = "https://aip.baidubce.com/oauth/2.0/token"
    params = {
        "grant_type": "client_credentials",
        "client_id": API_KEY,
        "client_secret": SECRET_KEY
    }
    try:
        response = requests.post(url, params=params)
        return response.json().get("access_token")
    except Exception as e:
        message_queue.put((text_widget, f"获取access token失败: {e}"))
        return None


# 执行大模型检测
def perform_detection(text_widget):
    message_queue.put((text_widget, "开始检测"))
    if not auto_open_serial(text_widget):
        message_queue.put((text_widget, "大模型检测因未找到可用串口终止"))
        return
    # 获取access token
    access_token = get_access_token(text_widget)
    if not access_token:
        close_serial(text_widget)
        message_queue.put((text_widget, "无法获取access token，请检查API密钥"))
        return

    # 完整的API请求URL
    url = "https://aip.baidubce.com/rest/2.0/image-classify/v1/animal?access_token=" + access_token

    # 读取图像文件，并转换为base64编码的字符串
    try:
        with open(photo_path, "rb") as image_file:
            # 将图像数据编码为base64字符串
            encoded_image = base64.b64encode(image_file.read())
    except Exception as e:
        close_serial(text_widget)
        message_queue.put((text_widget, f"读取图片失败: {e}"))
        return

    # 准备请求数据
    payload = {
        'image': encoded_image.decode('utf-8')  # 确保将bytes转换为str
    }

    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Accept': 'application/json'
    }

    # 发送POST请求
    try:
        response = requests.post(url, data=payload, headers=headers, timeout=10)
        response_json = response.json()

        # 将结果发送到output_queue和文本组件
        output = ""
        if 'result' in response_json:
            for animal in response_json['result']:
                result_str = f"Animal: {animal['name']}, Probability: {animal['score']}"
                output += result_str + "\n"
                output_queue.put(result_str)
        else:
            error_msg = f"No 'Animal' result in response: {response_json}"
            output += error_msg + "\n"
            output_queue.put(error_msg)

        if "Animal" in output:
            message_queue.put((text_widget, "检测到动物分类结果"))
            if "虫" in output:
                message_queue.put((text_widget, "检测到害虫分类"))
                match = re.search(r"Animal: .*?, Probability: (\d+\.\d+)", output)
                if match:
                    probability = float(match.group(1))
                    message_queue.put((text_widget, f"检测到害虫，置信度: {probability}"))
                    if probability > 0.5:
                        zdjs(text_widget)
                        send_serial_command('O', text_widget)  # 发送除虫指令
                        message_queue.put((text_widget, "已发送除虫指令O"))
                    else:
                        message_queue.put((text_widget, "置信度不足50%，不执行除虫"))
                else:
                    message_queue.put((text_widget, "未获取到置信度数据"))
            else:
                message_queue.put((text_widget, "未检测到害虫分类"))
        else:
            message_queue.put((text_widget, "未检测到动物分类"))

    except Exception as e:
        message_queue.put((text_widget, f"API请求失败: {e}"))
    finally:
        close_serial(text_widget)


# 大模型计算
def main(text_widget):
    capture_photo(text_widget)

    def perform_and_log_detection():
        try:
            perform_detection(text_widget)
        except Exception as e:
            message_queue.put((text_widget, f"执行大模型检测时出错: {e}"))

    executor.submit(perform_and_log_detection)


# 新增：摄像头变焦按钮
ZOOM_STEP = 0.1
MIN_ZOOM = 1.0
MAX_ZOOM = 3.0
current_zoom = 1.0  # 初始变焦值


def zoom_in():
    global current_zoom
    current_zoom = min(current_zoom + ZOOM_STEP, MAX_ZOOM)


def zoom_out():
    global current_zoom
    current_zoom = max(current_zoom - ZOOM_STEP, MIN_ZOOM)


# 更新视频画面
def update_frame():
    ret, frame = cap.read()
    if ret:
        # 根据当前变焦值裁剪图像
        height, width = frame.shape[:2]
        center_x, center_y = width // 2, height // 2
        new_width = int(width / current_zoom)
        new_height = int(height / current_zoom)
        left = max(0, center_x - new_width // 2)
        top = max(0, center_y - new_height // 2)
        right = min(width, center_x + new_width // 2)
        bottom = min(height, center_y + new_height // 2)
        cropped_frame = frame[top:bottom, left:right]

        # 调整裁剪后的图像大小以适应窗口
        resized_frame = cv2.resize(cropped_frame, (width, height))

        # 转换图像格式以适应Tkinter
        frame_rgb = cv2.cvtColor(resized_frame, cv2.COLOR_BGR2RGB)
        frame_image = Image.fromarray(frame_rgb)
        frame_photo = ImageTk.PhotoImage(frame_image)
        video_label.config(image=frame_photo)
        video_label.image = frame_photo
    # 持续调用该函数
    video_label.after(10, update_frame)


# 拍摄照片
def capture_photo(text_widget):
    ret, frame = cap.read()
    if ret:
        cv2.imwrite(photo_path, frame)
        message_queue.put((text_widget, f"照片已保存到 {photo_path}"))


# 每隔三分钟拍照
def auto_capture(text_widget):
    capture_photo(text_widget)  # 调用拍照函数
    root.after(180000, auto_capture, text_widget)  # 180000 毫秒 = 3 分钟


# 机器人运动控制面板类
class SerialApp:
    def __init__(self, root, text_widget):
        self.root = root
        self.root.title("机器人运动控制面板")
        self.serial_port = None
        self.serial_open = False
        self.pressed_buttons = set()
        self.root.configure(bg='#f0f8ff')
        self.text_widget = text_widget
        threading.Thread(target=self.init_serial, daemon=True).start()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self.close_app)
        self.update_text_loop()  # 启动文本框更新循环

    def init_serial(self):
        success = auto_open_serial(self.text_widget)
        if success:
            self.serial_open = True
        else:
            message_queue.put((self.text_widget, "初始化串口失败，请检查连接"))

    def create_widgets(self):
        # 标题
        title_label = tk.Label(self.root, text="机器人运动控制面板", font=("Inter", 32, "bold"), bg="#f0f8ff",
                               fg="#003366")
        title_label.pack(pady=30)

        # 上部控制区
        control_frame = tk.Frame(self.root, bg="#f0f8ff")
        control_frame.pack(pady=20)

        # 自定义字体
        button_font = Font(family="Inter", size=18, weight="bold")

        # 控制按钮
        self.create_button(control_frame, "⬆ 前进", 'A', 0, 1, "#42A5F5", "#000000", button_font)
        self.create_button(control_frame, "⬇ 后退", 'B', 2, 1, "#42A5F5", "#000000", button_font)
        self.create_button(control_frame, "⬅ 左转", 'D', 1, 0, "#42A5F5", "#000000", button_font)
        self.create_button(control_frame, "➡ 右转", 'C', 1, 2, "#42A5F5", "#000000", button_font)
        self.create_button(control_frame, "⏹ 停止", 'E', 1, 1, "#FF7043", "#000000", button_font)

        # 中部功能区
        function_frame = tk.Frame(self.root, bg="#f0f8ff")
        function_frame.pack(pady=10)

        self.create_button(function_frame, "🎮 遥控器", 'K', 0, 0, "#42A5F5", "#000000", button_font)
        self.create_button(function_frame, "🤖 自动控制", 'G', 0, 1, "#42A5F5", "#000000", button_font)
        self.create_button(function_frame, "💧 水泵开", 'Y', 1, 0, "#FFCA28", "#000000", button_font)
        self.create_button(function_frame, "💧 水泵关", 'Z', 1, 1, "#FFCA28", "#000000", button_font)
        self.create_button(function_frame, "🚨 警示灯开", 'N', 2, 0, "#FFEE58", "#000000", button_font)
        self.create_button(function_frame, "🚨 警示灯关", 'M', 2, 1, "#FFEE58", "#000000", button_font)

        # 底部退出按钮
        exit_button = tk.Button(self.root, text="退出 ❌", width=15, height=3, command=self.close_app, bg="#EF5350",
                                fg="#000000", font=("Inter", 20, "bold"), relief="flat")
        exit_button.pack(side="bottom", pady=30)

    def create_button(self, parent, text, command_char, row, col, bg_color, fg_color, font):
        button = tk.Button(
            parent, text=text, width=15, height=3, bg=bg_color, fg=fg_color, font=font,
            activebackground=bg_color, activeforeground=fg_color, relief="flat", bd=0
        )
        button.grid(row=row, column=col, padx=15, pady=15)
        button.bind("<ButtonPress>", lambda event: self.start_sending(command_char))
        button.bind("<ButtonRelease>", lambda event: self.stop_sending(command_char))
        button.bind("<Enter>", lambda event: button.config(bg=lighten_color(bg_color)))
        button.bind("<Leave>", lambda event: button.config(bg=bg_color))

    def start_sending(self, char):
        if self.serial_open and serial_port:
            self.pressed_buttons.add(char)
            self.serial_port_write(char)
        else:
            message_queue.put((self.text_widget, "串口未打开，无法发送命令"))

    def stop_sending(self, char):
        if char in self.pressed_buttons:
            self.pressed_buttons.remove(char)
            if not self.pressed_buttons:
                self.serial_port_write('E')

    def serial_port_write(self, char):
        if self.serial_open and serial_port:
            send_serial_command(char, self.text_widget)
        else:
            message_queue.put((self.text_widget, "串口未打开，无法发送命令"))

    def close_app(self):
        self.stop_sending('dummy')
        # 关闭机械运动窗口时关闭串口
        close_serial(self.text_widget)
        self.root.destroy()

    def close_serial(self):
        if self.serial_open:
            close_serial(self.text_widget)
            self.serial_open = False

    def update_text_loop(self):
        update_text(self.text_widget)
        self.root.after(50, self.update_text_loop)


def lighten_color(hex_color, factor=0.2):
    hex_color = hex_color.lstrip('#')
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)

    r = min(255, int(r + (255 - r) * factor))
    g = min(255, int(g + (255 - g) * factor))
    b = min(255, int(b + (255 - b) * factor))

    return "#{:02x}{:02x}{:02x}".format(r, g, b)


# 读取串口数据的函数
def read_serial_data(text_widget):
    global serial_port
    while True:
        with serial_lock:
            if serial_port and serial_port.is_open:
                try:
                    data = serial_port.readline().decode('utf-8').strip()
                    if data:
                        message_queue.put((text_widget, f"Received from serial: {data}"))
                except Exception as e:
                    message_queue.put((text_widget, f"读取串口数据时出错: {e}"))
        time.sleep(0.1)


# 创建主窗口
root = tk.Tk()
root.title("农机视觉主窗口")
root.geometry("1280x720")
root.configure(bg="#F0F8FF")  # 背景色

# 新增：定时检测时间间隔变量
interval_var = tk.StringVar()
interval_var.set(5)  # 默认 5 分钟

# 使用Ttk风格
style = ttk.Style()
style.configure("TButton", font=("Inter", 16), padding="12 24", background="#2196F3", foreground="#000000",
                relief="flat", bd=0, borderwidth=0, highlightthickness=0)
style.map("TButton", background=[("active", "#1976D2"), ("hover", lighten_color("#2196F3"))])

# 创建标题
title_label = tk.Label(root, text="农机视觉主窗口", font=("Inter", 36, "bold"), bg="#F0F8FF", fg="#003366")
title_label.pack(pady=30)

# 创建Frame用于组织按钮
button_frame = tk.Frame(root, bg="#F0F8FF")
button_frame.pack(side=tk.RIGHT, padx=50, pady=20, fill=tk.Y)

# 创建按钮并放置到Frame上
button1 = ttk.Button(button_frame, text="机械运动", command=lambda: SerialApp(tk.Toplevel(), text_widget))
button1.pack(pady=20, fill=tk.X)

button4 = ttk.Button(button_frame, text="拍照", command=lambda: capture_photo(text_widget))
button4.pack(pady=20, fill=tk.X)

button3 = ttk.Button(button_frame, text="大模型计算", command=lambda: main(text_widget))
button3.pack(pady=20, fill=tk.X)

# 新增：调整定时检测时间的输入框和按钮
interval_label = tk.Label(button_frame, text="定时检测间隔 (分钟):", font=("Inter", 14), bg="#F0F8FF", fg="#003366")
interval_label.pack(pady=10)
interval_spinbox = Spinbox(button_frame, from_=1, to=60, textvariable=interval_var, font=("Inter", 14))
interval_spinbox.pack(pady=5)
interval_button = ttk.Button(button_frame, text="应用间隔", command=lambda: periodic_check(text_widget))
interval_button.pack(pady=20)

zoom_in_button = ttk.Button(button_frame, text="变焦放大", command=zoom_in)
zoom_in_button.pack(pady=10)
zoom_out_button = ttk.Button(button_frame, text="变焦缩小", command=zoom_out)
zoom_out_button.pack(pady=10)

# 用于显示摄像头画面的Label
video_label = Label(root, bg="#d9e6f2", borderwidth=2, relief="groove")
video_label.pack(side=tk.LEFT, padx=20, pady=20)

# 创建分隔符
separator = ttk.Separator(root, orient="horizontal")
separator.pack(fill="x", pady=10)

# 创建Text组件用于显示damoxing.py的输出
text_widget = scrolledtext.ScrolledText(root, height=15, width=200, wrap=tk.WORD, font=("Inter", 12), bg="#FFFFFF",
                                        fg="#333333", bd=2, relief="solid")
text_widget.pack(fill="both", padx=20, pady=10, expand=True)
text_widget.delete("1.0", tk.END)

# 启动视频更新
update_frame()

# 启动定时拍照
auto_capture(text_widget)


# 定时检测函数
def periodic_check(text_widget):
    capture_photo(text_widget)
    executor.submit(perform_detection, text_widget)
    interval = int(interval_var.get()) * 60 * 1000
    log_message(text_widget, f"自动检测时间为 {interval_var.get()} 分钟")
    root.after(interval, periodic_check, text_widget)


# 启动定时检测
root.after(300000, periodic_check, text_widget)

# 启动文本更新
update_text(text_widget)

# 启动消息队列处理
process_message_queue()


def on_closing():
    try:
        executor.shutdown(wait=False)
        global cap
        if cap and cap.isOpened():
            cap.release()
        with serial_lock:
            if serial_port and serial_port.is_open:
                close_serial(text_widget)
        root.destroy()
    except Exception as e:
        print(f"关闭程序时出错: {e}")


root.protocol("WM_DELETE_WINDOW", on_closing)

# 启动事件循环
root.mainloop()

