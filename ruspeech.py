import os 
import threading
import queue
import time
import sys
import ctypes
import winsound
import numpy as np
import sounddevice as sd
import keyboard
import pyperclip
import onnx_asr
import tkinter as tk
import winreg


# --- ИНИЦИАЛИЗАЦИЯ ---
mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "GemmaWhisper_V3_Stable")
if ctypes.windll.kernel32.GetLastError() == 183: 
    print("Программа уже запущена")
    time.sleep(1)
    sys.exit(0)

def set_console_title(title):
    try:
        ctypes.windll.kernel32.SetConsoleTitleW(title)
    except Exception: 
        pass
def get_target_key():
    try:
        # Проверяем реестр один раз при старте
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Keyboard") as key:
            val, _ = winreg.QueryValueEx(key, "PrintScreenKeyForSnippingEnabled")
            if val == 1:
                return "left windows"
    except Exception:
        pass
    return "print screen"

TARGET_KEY = get_target_key()
class CursorOverlay:
    def __init__(self):
        self.queue = queue.Queue()
        self.running = True
        self.root = None
        set_console_title("🎙️ Диктовка")

    def _create_window(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.config(bg='white')
        self.root.wm_attributes("-transparentcolor", "white")
        # Делаем индикатор чуть прозрачным (неоновый эффект)
        self.root.attributes("-alpha", 0.85)
        self.root.withdraw() 

        # 1. Холст с небольшим запасом для тени
        self.canvas = tk.Canvas(self.root, width=36, height=36, bg='white', highlightthickness=0)


        # 3. Основной ЦВЕТНОЙ КРУГ
        # Центр теперь смещен в (18, 18) из-за размера холста 36
        self.indicator = self.canvas.create_oval(4, 4, 30, 30, fill="red", outline="white", width=2)

        # 4. Символ ВНУТРИ (строго по центру основного круга)
        self.inner_icon = self.canvas.create_text(
            17, 17, # Центр круга 4-30 это 17
            text="", 
            font=("Arial", 11, "bold"), 
            fill="black",
            anchor="center"
        )

        self.canvas.pack()
        
        self.canvas.pack()
        self._update_loop()
        self.root.mainloop()

    def _update_loop(self):
        if not self.running:
            if self.root: 
                self.root.destroy()
            return
        try:
            while True:
                status = self.queue.get_nowait()
                if status == "hidden":
                    self.root.withdraw()
                    set_console_title("🎙️ Диктовка")
                else:
                    self._apply_theme(status)
                    self.root.deiconify()
        except queue.Empty: 
            pass
        
        if self.root.winfo_viewable():
            # Позиция у кончика курсора
            x, y = self.root.winfo_pointerx() + 12, self.root.winfo_pointery() + 12
            self.root.geometry(f"+{x}+{y}")
        
        self.root.after(15, self._update_loop)

    def _apply_theme(self, status):
        # Настройка: Цвет круга, Текст внутри, Заголовок окна
        themes = {
            "recording":  {"color": "#FF3B30", "inner": "", "title": "🔴 ЗАПИСЬ..."}, # Насыщенный красный
            "paused":     {"color": "#EFF308", "inner": "⏸️", "title": "⏸️ ПАУЗА"},    # Яркий желтый
            "processing": {"color": "#34C759", "inner": "⏳", "title": "⏳ ОБРАБОТКА..."} # Сочный зеленый
        }
        
        data = themes.get(status, {"color": "gray", "inner": "?", "title": "🎙️ GIGA"})
        
        # Меняем цвет фигуры у курсора (Tkinter это делает ЦВЕТНЫМ)
        self.canvas.itemconfig(self.indicator, fill=data["color"])
        # Меняем внутренний символ (ч/б текст поверх цвета)
        self.canvas.itemconfig(self.inner_icon, text=data["inner"])
        # Меняем заголовок в панели задач (Windows рисует ЦВЕТНЫЕ эмодзи)
        set_console_title(data["title"])

    def set_status(self, status):
        self.queue.put(status)

    def stop(self):
        self.running = False

# --- ИНИЦИАЛИЗАЦИЯ ---
def start_indicator():
    overlay = CursorOverlay()
    t = threading.Thread(target=overlay._create_window, daemon=True)
    t.start()
    return overlay

# Создаем глобальный объект индикатора
indicator = start_indicator()

print("Загрузка модели...")
giga_model = onnx_asr.load_model("gigaam-v3-e2e-rnnt")

# Состояния
is_recording = False

is_paused = False
audio_buffer = []
audio_queue = queue.Queue()
esc_presses = []
indicator = None 
# Состояния для коррекции
auto_correct_mode = False
scroll_lock_presses = []
#Использование в проверке того, был ли пробел после предыдущего распознанного текста.
# Храним ID последнего активного окна и последний символ
last_hwnd = None
last_char = ""

def get_active_window_handle():
    """Получает идентификатор текущего активного окна в Windows"""
    return ctypes.windll.user32.GetForegroundWindow()

def play_sound(action):
    s = {"start": [(440, 100), (660, 100)], "stop": [(660, 100), (440, 100)], 
         "pause": [(300, 150)], "resume": [(800, 150)], "fix": [(1000, 80), (1200, 80)],
         "mode_on": [(600, 100), (800, 100), (1000, 100)], 
         "mode_off": [(1000, 100), (800, 100), (600, 100)],
         "copy": [(1200, 50), (1400, 50)]}
    for f, d in s.get(action, []): 
        winsound.Beep(f, d)





# --- ФУНКЦИИ В ОТДЕЛЬНЫХ ПОТОКАХ ---



def async_toggle_recording():
    global is_recording, audio_buffer, is_paused, indicator
    if not is_recording:
        play_sound("start")
        print("Начало записи...")
        #push_toast("Диктовка", "Начало записи...")
        if indicator:
            indicator.set_status("recording")
        is_recording, is_paused, audio_buffer = True, False, []
        threading.Thread(target=record_loop, daemon=True).start()
    else:
        is_recording = False
        play_sound("stop")
        print("Завершение записи...")
        #push_toast("Диктовка", "Запись остановлена. Обработка...")
        if indicator:
            indicator.set_status("processing")
        threading.Thread(target=process_audio, daemon=True).start()

def record_loop():
    with sd.InputStream(samplerate=16000, channels=1, callback=lambda i,f,t,s: audio_queue.put(i.copy()) if is_recording and not is_paused else None):
        while is_recording:
            while not audio_queue.empty(): 
                audio_buffer.append(audio_queue.get())
            time.sleep(0.1)

def process_audio():
    global audio_buffer
    global last_char
    global last_hwnd
    if not audio_buffer: 
        return
    
    global indicator
    try:
                # Проверяем активное окно ДО вставки
        current_hwnd = get_active_window_handle()
                # Если окно сменилось — сбрасываем память символа
        if current_hwnd != last_hwnd:
            last_char = ""
            last_hwnd = current_hwnd
        # Склеиваем и гарантируем float32 (GigaAM критичен к типу)
        data = np.concatenate(audio_buffer, axis=0).flatten().astype(np.float32)
        # сразу освобождаем глобальный буфер
        audio_buffer = [] 
        # Распознавание через ONNX (сразу получаем готовый текст)
        # Параметры beam_size и language здесь не нужны, модель заточена под RU
        text = giga_model.recognize(data, sample_rate=16000).strip()
        if text:
            # 1. ПРОВЕРКА: Нужен ли пробел ПЕРЕД текущим текстом?
            # Если предыдущий текст закончился не на пробел, добавляем его в начало
            if last_char and not last_char.isspace():
                text = " " + text

            pyperclip.copy(text)

            keyboard.press_and_release('ctrl+v')
                        # 3. ЗАПОМИНАЕМ последний символ для следующего раза
            last_char = text[-1]
            print(f"Распознано: {text}")
   
    except Exception as e:
        print(f"Ошибка распознавания: {e}")
    finally:
        # Когда обработка завершена (успешно или с ошибкой) — скрываем значок
        if indicator: 
            audio_buffer = [] 
            indicator.set_status("hidden")

# --- ОБРАБОТЧИК КЛАВИШ ---
def on_key_event(e):
    global is_paused, esc_presses, is_recording, auto_correct_mode, scroll_lock_presses, last_char
    
    if e.event_type == 'down':
 
            
        if e.name == TARGET_KEY: 
            if TARGET_KEY == "print screen" or keyboard.is_pressed('ctrl'):
                async_toggle_recording()


        elif e.name == 'right ctrl' and is_recording: 
            is_paused = not is_paused
            play_sound("pause" if is_paused else "resume")
            print("Пауза") if is_paused else print("Продолжение")
            if indicator:
                indicator.set_status("paused" if is_paused else "recording")
        if e.name == 'enter':
            last_char = ""
        elif e.name == 'esc':
            now = time.time()
            if len(esc_presses) > 0 and now - esc_presses[-1] > 1.5:
                esc_presses.clear()
            esc_presses.append(now)
            if len(esc_presses) >= 3:
                winsound.Beep(200, 600)
                os._exit(0)
if __name__ == "__main__":
    keyboard.hook(on_key_event)
    indicator = start_indicator()
    indicator.set_status("hidden") # Сразу прячем после запуска
    print("Система готова.")
    if TARGET_KEY == "print screen":
        print("Print Screen    - Запись")
    else :
        print("Ctrl + WIN      - Запись")


    print("Right Ctrl      - Пауза")
    print("Esc три раза    - Выход")
    keyboard.wait()