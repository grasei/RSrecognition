import os 
import threading
import queue
import time
import sys
import ctypes
import winsound
import ctypes.wintypes
import numpy as np
import sounddevice as sd
import keyboard
import pyperclip
import onnx_asr
import tkinter as tk


# --- ИНИЦИАЛИЗАЦИЯ ---
mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "GemmaWhisper_V3_Stable")
if ctypes.windll.kernel32.GetLastError() == 183: 
    print("Программа уже запущена")
    time.sleep(1)
    sys.exit(0)

class CursorOverlay:
    def __init__(self):
        self.root = None
        self.queue = queue.Queue()
        self.running = True

    def _create_window(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.config(bg='white')
        self.root.wm_attributes("-transparentcolor", "white")
        
        # Скрываем окно сразу после создания
        self.root.withdraw() 

        self.canvas = tk.Canvas(self.root, width=22, height=22, bg='white', highlightthickness=0)
        self.indicator = self.canvas.create_oval(2, 2, 20, 20, fill="gray", outline="black")
        self.canvas.pack()

        self._update_loop()
        self.root.mainloop()

    def _update_loop(self):
        if not self.running:
            self.root.destroy()
            return
        
        try:
            while True:
                new_status = self.queue.get_nowait()
                if new_status == "hidden":
                    self.root.withdraw()  # ПОЛНОСТЬЮ убираем окно и процесс отрисовки
                else:
                    self._change_color(new_status)
                    self.root.deiconify() # Возвращаем окно на экран
        except queue.Empty:
            pass
        
        # Если окно активно, двигаем его
        if self.root.state() == "normal":
            x = self.root.winfo_pointerx() + 18
            y = self.root.winfo_pointery() + 18
            self.root.geometry(f"+{x}+{y}")
        
        self.root.after(10, self._update_loop)

    def _change_color(self, status):
        colors = {
            "recording": "red",
            "paused": "yellow",
            "processing": "#00FF00" # Зеленый
        }
        color = colors.get(status, "gray")
        self.canvas.itemconfig(self.indicator, fill=color, outline="black")

    def set_status(self, status):
        self.queue.put(status)


# --- Использование ---
def start_indicator():
    overlay = CursorOverlay()
    t = threading.Thread(target=overlay._create_window, daemon=True)
    t.start()
    return overlay
def stop_indicator():
    indicator.stop()

print("Загрузка моделей...")
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
correction_queue = queue.Queue()

# Очередь для уведомлений
notification_queue = queue.Queue()

# Блокировка для безопасной работы с буфером обмена
# Гарантирует, что автокоррекция не перезапишет текст во время вставки распознанного
clipboard_lock = threading.Lock()

def play_sound(action):
    s = {"start": [(440, 100), (660, 100)], "stop": [(660, 100), (440, 100)], 
         "pause": [(300, 150)], "resume": [(800, 150)], "fix": [(1000, 80), (1200, 80)],
         "mode_on": [(600, 100), (800, 100), (1000, 100)], 
         "mode_off": [(1000, 100), (800, 100), (600, 100)],
         "copy": [(1200, 50), (1400, 50)]}
    for f, d in s.get(action, []): 
        winsound.Beep(f, d)



def push_toast(title, body):
    notification_queue.put((title, body))

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
    if not audio_buffer: 
        return


    global indicator
    try:
        # Склеиваем и гарантируем float32 (GigaAM критичен к типу)
        data = np.concatenate(audio_buffer, axis=0).flatten().astype(np.float32)
        # сразу освобождаем глобальный буфер
        audio_buffer = [] 
        # Распознавание через ONNX (сразу получаем готовый текст)
        # Параметры beam_size и language здесь не нужны, модель заточена под RU
        text = giga_model.recognize(data, sample_rate=16000).strip()
        if text:
            # БЛОКИРОВКА: Вставка происходит атомарно
            with clipboard_lock:
                pyperclip.copy(text)
                keyboard.press_and_release('ctrl+v')
            
            print(f"Распознано: {text}")
            
            if auto_correct_mode:
                print("Добавлено в очередь на коррекцию.")
                correction_queue.put(text)
    except Exception as e:
        print(f"Ошибка распознавания: {e}")
    finally:
        # Когда обработка завершена (успешно или с ошибкой) — скрываем значок
        if indicator: 
            audio_buffer = [] 
            indicator.set_status("hidden")

# --- ОБРАБОТЧИК КЛАВИШ ---
def on_key_event(e):
    global is_paused, esc_presses, is_recording, auto_correct_mode, scroll_lock_presses
    
    if e.event_type == 'down':
 
            
        if e.name == 'print screen': 
            async_toggle_recording()


        elif e.name == 'right ctrl' and is_recording: 
            is_paused = not is_paused
            play_sound("pause" if is_paused else "resume")
            print("Пауза") if is_paused else print("Продолжение")
            if indicator:
                indicator.set_status("paused" if is_paused else "recording")
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
    print("Print Screen    - Запись")
    print("Right Ctrl      - Пауза")
    print("Esc три раза    - Выход")
    keyboard.wait()