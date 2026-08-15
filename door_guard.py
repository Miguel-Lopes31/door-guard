"""
Door Guard - app de terminal
=============================
Monitora sua webcam, detecta abertura/fechamento de porta, e controla
automaticamente a "OBS Virtual Camera" (usada no Discord/Meet/Zoom) e,
opcionalmente, o microfone do Windows.

Pre-requisitos (so nesta fase, antes de virar .exe):
    pip install opencv-python numpy pyvirtualcam pycaw comtypes

Como usar:
    python door_guard.py
"""

import os
import sys
import json
import time

import cv2
import numpy as np
import pyvirtualcam
from pyvirtualcam import PixelFormat

# Mic mute e opcional: se o pycaw nao estiver instalado, o app continua
# funcionando normalmente, so a opcao de mudo automatico fica desativada.
try:
    from ctypes import cast, POINTER
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    PYCAW_AVAILABLE = True
except ImportError:
    PYCAW_AVAILABLE = False

# --------------------------------------------------------------------------
# Configuracao persistente (salva num arquivo config.json ao lado do app)
# --------------------------------------------------------------------------

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
LOG_PATH = os.path.join(BASE_DIR, "historico.txt")

DEFAULT_CONFIG = {
    "camera_index": 0,
    "threshold": 25,
    "min_change_ratio": 0.15,
    "confirm_open_seconds": 1.2,
    "confirm_closed_seconds": 0.3,
    "bg_update_rate": 0.02,
    "target_fps": 30,
    "video_block_enabled": True,
    "mic_mute_enabled": False,
    "roi": None,  # [x, y, w, h] - definido na primeira calibracao
}


def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            # garante que chaves novas (de versoes futuras) existam
            for k, v in DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            return cfg
        except (json.JSONDecodeError, OSError):
            print("[AVISO] config.json corrompido, restaurando padroes.")
    return dict(DEFAULT_CONFIG)


def log_event(event, details=""):
    """Grava uma linha no historico.txt com data, hora, tipo de evento e detalhes."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {event}"
    if details:
        line += f" - {details}"
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as e:
        print(f"[AVISO] Nao consegui gravar no historico: {e}")


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        return True
    except OSError as e:
        print(f"[ERRO] Nao consegui salvar as configuracoes: {e}")
        return False


# --------------------------------------------------------------------------
# Utilidades de terminal
# --------------------------------------------------------------------------

def clear():
    os.system("cls" if os.name == "nt" else "clear")


def pause():
    input("\nPressione ENTER para continuar...")


def header(title):
    clear()
    print("=" * 50)
    print(f"  DOOR GUARD - {title}")
    print("=" * 50)
    print()


# --------------------------------------------------------------------------
# Controle de microfone (Windows / pycaw)
# --------------------------------------------------------------------------

def set_mic_mute(mute: bool):
    """Muta/desmuta o microfone padrao do Windows. Retorna (sucesso, mensagem)."""
    if not PYCAW_AVAILABLE:
        return False, "pycaw nao instalado (pip install pycaw comtypes)"
    try:
        mic = AudioUtilities.GetMicrophone()
        interface = mic.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))
        volume.SetMute(1 if mute else 0, None)
        return True, "ok"
    except Exception as e:
        return False, str(e)


# --------------------------------------------------------------------------
# Visao computacional (deteccao da porta)
# --------------------------------------------------------------------------

def select_roi(frame):
    print("Desenhe um retangulo ao redor da porta e aperte ENTER/ESPACO.")
    roi = cv2.selectROI("Preview (so voce ve isso)", frame, showCrosshair=True)
    x, y, w, h = roi
    if w == 0 or h == 0:
        raise ValueError("ROI invalida.")
    return int(x), int(y), int(w), int(h)


def make_static_noise(shape):
    noise = np.random.randint(0, 255, shape, dtype=np.uint8)
    for _ in range(3):
        row = np.random.randint(0, shape[0])
        noise[row:row + 2, :] = 255
    cv2.putText(noise, "SEM SINAL", (shape[1] // 2 - 90, shape[0] // 2),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (80, 80, 80), 2)
    return noise


def calibrate_background(cap, x, y, w, h, seconds=3):
    print(f"Calibrando fundo com a porta FECHADA por {seconds}s... nao mexa nela.")
    samples = []
    t0 = time.time()
    while time.time() - t0 < seconds:
        ok, frame = cap.read()
        if not ok:
            continue
        roi_gray = cv2.cvtColor(frame[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY)
        roi_gray = cv2.GaussianBlur(roi_gray, (5, 5), 0)
        samples.append(roi_gray.astype(np.float32))
        cv2.imshow("Preview (so voce ve isso)", frame)
        cv2.waitKey(1)
    print("Calibracao concluida.")
    return np.mean(samples, axis=0)


def open_camera(cam_index):
    cap = cv2.VideoCapture(cam_index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        return None
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    ok = False
    for _ in range(60):
        ok, frame = cap.read()
        if ok:
            time.sleep(0.05)
    ok, frame = cap.read()
    if not ok:
        cap.release()
        return None
    return cap


def run_monitor(cfg):
    header("Monitoramento")
    print("Abrindo webcam...")
    log_event("MONITORAMENTO_INICIADO",
              f"camera_index={cfg['camera_index']}, "
              f"bloqueio_video={cfg['video_block_enabled']}, "
              f"mudo_mic={cfg['mic_mute_enabled']}")
    cap = open_camera(cfg["camera_index"])
    if cap is None:
        print("[ERRO] Nao consegui abrir a webcam.")
        print("Verifique se outro app (Discord/Meet/Teams/Camera) nao esta usando ela,")
        print("e confira se 'camera_index' esta correto nas Configuracoes.")
        log_event("ERRO_CAMERA", "Falha ao abrir a webcam")
        pause()
        return

    ok, frame = cap.read()
    height, width = frame.shape[:2]

    roi = cfg.get("roi")
    if roi:
        print(f"Usando ROI salva anteriormente: {roi}")
        print("(escolha 'Recalibrar porta' no menu de Configuracoes se ela mudou de lugar)")
        x, y, w, h = roi
    else:
        x, y, w, h = select_roi(frame)
        cfg["roi"] = [x, y, w, h]
        save_config(cfg)

    bg_model = calibrate_background(cap, x, y, w, h)

    door_open = False
    pending_state = door_open
    pending_since = None
    door_open_count = 0
    door_opened_at = None
    total_open_seconds = 0.0

    try:
        with pyvirtualcam.Camera(width=width, height=height, fps=cfg["target_fps"],
                                  fmt=PixelFormat.BGR) as vcam:
            print(f"[OK] Camera virtual ativa: {vcam.device}")
            print("[OK] Selecione 'OBS Virtual Camera' no Discord/Meet/Zoom.")
            print("Monitorando... ('q' no preview para voltar ao menu | 'r' recalibrar)\n")

            mic_currently_muted = False

            while True:
                ok, frame = cap.read()
                if not ok:
                    print("[ERRO] Perdi o sinal da webcam durante o monitoramento.")
                    break

                roi_gray = cv2.cvtColor(frame[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY)
                roi_gray_blur = cv2.GaussianBlur(roi_gray, (5, 5), 0)
                diff = cv2.absdiff(roi_gray_blur.astype(np.float32), bg_model)
                _, mask = cv2.threshold(diff, cfg["threshold"], 255, cv2.THRESH_BINARY)
                change_ratio = np.count_nonzero(mask) / mask.size
                current_reading = change_ratio > cfg["min_change_ratio"]
                now = time.time()

                if current_reading == door_open:
                    pending_state = door_open
                    pending_since = None
                else:
                    if pending_state != current_reading or pending_since is None:
                        pending_state = current_reading
                        pending_since = now
                    required = (cfg["confirm_open_seconds"] if current_reading
                                else cfg["confirm_closed_seconds"])
                    if now - pending_since >= required:
                        door_open = current_reading
                        pending_since = None
                        print(f"[{time.strftime('%H:%M:%S')}] Porta {'ABERTA' if door_open else 'FECHADA'}")

                        if door_open:
                            door_opened_at = now
                            door_open_count += 1
                            log_event("PORTA_ABERTA",
                                      f"confirmado_apos={required:.1f}s, "
                                      f"ocorrencia_num={door_open_count}, "
                                      f"bloqueio_video={cfg['video_block_enabled']}, "
                                      f"mudo_mic={cfg['mic_mute_enabled']}")
                        else:
                            duration = (now - door_opened_at) if door_opened_at else 0.0
                            total_open_seconds += duration
                            log_event("PORTA_FECHADA", f"tempo_aberta={duration:.1f}s")
                            door_opened_at = None

                        if cfg["mic_mute_enabled"]:
                            ok_mic, msg = set_mic_mute(door_open)
                            if ok_mic:
                                mic_currently_muted = door_open
                                print(f"    -> Microfone {'MUTADO' if door_open else 'DESMUTADO'}")
                                log_event("MIC_MUTADO" if door_open else "MIC_DESMUTADO")
                            else:
                                print(f"    -> [ERRO] Nao consegui controlar o microfone: {msg}")
                                log_event("ERRO_MIC", msg)

                if not door_open:
                    bg_model = ((1 - cfg["bg_update_rate"]) * bg_model
                                + cfg["bg_update_rate"] * roi_gray_blur.astype(np.float32))

                if cfg["video_block_enabled"] and door_open:
                    output = make_static_noise(frame.shape)
                else:
                    output = frame

                vcam.send(output)
                vcam.sleep_until_next_frame()

                preview = frame.copy()
                cv2.rectangle(preview, (x, y), (x + w, y + h),
                               (0, 0, 255) if door_open else (0, 255, 0), 2)
                cv2.imshow("Preview (so voce ve isso)", preview)

                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break
                elif key == ord("r"):
                    x, y, w, h = select_roi(frame)
                    cfg["roi"] = [x, y, w, h]
                    save_config(cfg)
                    bg_model = calibrate_background(cap, x, y, w, h)
                    door_open = False
                    pending_state = False
                    pending_since = None

            # ao sair do monitoramento, garante que o microfone nao fique mutado
            if cfg["mic_mute_enabled"] and mic_currently_muted:
                set_mic_mute(False)

    except Exception as e:
        print(f"[ERRO] Problema ao iniciar a camera virtual: {e}")
        print("Confira se o OBS Studio foi instalado (mesmo sem precisar abrir).")
        log_event("ERRO_CAMERA_VIRTUAL", str(e))

    cap.release()
    cv2.destroyAllWindows()
    log_event("MONITORAMENTO_ENCERRADO",
              f"portas_detectadas={door_open_count}, "
              f"tempo_total_aberta={total_open_seconds:.1f}s")
    print("\nMonitoramento encerrado.")
    pause()


# --------------------------------------------------------------------------
# Menu de configuracoes (cada opcao explicada antes de pedir o valor)
# --------------------------------------------------------------------------

SETTINGS_INFO = [
    {
        "key": "threshold",
        "label": "Sensibilidade de deteccao (threshold)",
        "explain": ("Controla o quanto um pixel precisa mudar de cor pra ser contado\n"
                    "como 'diferente'. Valores baixos (ex: 10) deixam a deteccao mais\n"
                    "sensivel a pequenas mudancas (mas mais chance de falso positivo\n"
                    "com sombras/luz). Valores altos (ex: 40) exigem mudancas mais\n"
                    "fortes. Faixa recomendada: 15 a 40."),
        "type": int,
        "min": 1, "max": 100,
    },
    {
        "key": "min_change_ratio",
        "label": "Percentual minimo de area alterada",
        "explain": ("Percentual (0 a 1) da area da porta que precisa estar 'diferente'\n"
                    "para considerar que algo mudou. 0.15 = 15% da area. Diminuir\n"
                    "deixa mais sensivel (detecta mudancas menores); aumentar exige\n"
                    "uma mudanca mais evidente (ex: a porta bem aberta)."),
        "type": float,
        "min": 0.01, "max": 1.0,
    },
    {
        "key": "confirm_open_seconds",
        "label": "Tempo para confirmar 'porta aberta' (segundos)",
        "explain": ("Quanto tempo a mudanca precisa persistir antes do app considerar\n"
                    "que a porta realmente abriu. Valores maiores evitam que uma\n"
                    "pessoa passando rapido na frente da porta dispare o bloqueio\n"
                    "por engano. Recomendado: 0.8 a 2.0 segundos."),
        "type": float,
        "min": 0.1, "max": 10.0,
    },
    {
        "key": "confirm_closed_seconds",
        "label": "Tempo para confirmar 'porta fechada' (segundos)",
        "explain": ("Quanto tempo a imagem precisa ficar estavel de novo antes do app\n"
                    "voltar ao normal apos a porta fechar. Pode ser bem curto (0.2 a\n"
                    "0.5s), ja que nao ha problema em confirmar rapido que esta tudo\n"
                    "tranquilo novamente."),
        "type": float,
        "min": 0.05, "max": 5.0,
    },
    {
        "key": "bg_update_rate",
        "label": "Velocidade de adaptacao ao fundo",
        "explain": ("O app atualiza aos poucos sua nocao de 'como a porta fechada\n"
                    "parece', pra se adaptar a mudancas de luz ao longo do dia.\n"
                    "Valores baixos (ex: 0.02) adaptam devagar (mais estavel).\n"
                    "Valores altos adaptam rapido, mas podem 'esquecer' a porta\n"
                    "fechada rapido demais. Recomendado: 0.01 a 0.05."),
        "type": float,
        "min": 0.0, "max": 0.5,
    },
    {
        "key": "target_fps",
        "label": "FPS da camera virtual",
        "explain": ("Quantos quadros por segundo a camera virtual vai anunciar pro\n"
                    "Discord/Meet. 30 e um valor padrao seguro. Aumentar pode deixar\n"
                    "mais fluido, mas usa mais processamento."),
        "type": int,
        "min": 10, "max": 60,
    },
    {
        "key": "camera_index",
        "label": "Indice da webcam (dispositivo de video)",
        "explain": ("Qual dispositivo de video o Windows deve usar como sua webcam\n"
                    "real. Geralmente 0. Se voce tiver mais de uma camera (ou a OBS\n"
                    "Virtual Camera aparecer na lista), pode ser necessario mudar\n"
                    "para 1, 2, etc. Use a opcao de testar cameras no menu principal\n"
                    "se nao tiver certeza."),
        "type": int,
        "min": 0, "max": 10,
    },
]


def settings_menu(cfg):
    while True:
        header("Configuracoes")
        for i, item in enumerate(SETTINGS_INFO, start=1):
            print(f"{i}. {item['label']}: {cfg[item['key']]}")
        print(f"{len(SETTINGS_INFO) + 1}. Recalibrar posicao da porta (ROI)")
        print("0. Voltar ao menu principal")
        choice = input("\nEscolha uma opcao: ").strip()

        if choice == "0":
            return
        elif choice == str(len(SETTINGS_INFO) + 1):
            cfg["roi"] = None
            save_config(cfg)
            print("[OK] Na proxima vez que iniciar o monitoramento, voce vai poder")
            print("     desenhar a posicao da porta de novo.")
            pause()
            continue

        try:
            idx = int(choice) - 1
            item = SETTINGS_INFO[idx]
        except (ValueError, IndexError):
            print("[ERRO] Opcao invalida.")
            pause()
            continue

        header(item["label"])
        print(item["explain"])
        print(f"\nValor atual: {cfg[item['key']]}")
        print(f"Faixa permitida: {item['min']} a {item['max']}")
        new_val_str = input("Novo valor (ENTER para cancelar): ").strip()

        if not new_val_str:
            continue

        try:
            new_val = item["type"](new_val_str)
            if not (item["min"] <= new_val <= item["max"]):
                raise ValueError("fora da faixa permitida")
            cfg[item["key"]] = new_val
            if save_config(cfg):
                print(f"[OK] {item['label']} atualizado para {new_val}.")
                log_event("CONFIGURACAO_ALTERADA", f"{item['key']}={new_val}")
            pause()
        except ValueError as e:
            print(f"[ERRO] Valor invalido ({e}). Nada foi alterado.")
            pause()


def test_cameras():
    header("Testar cameras disponiveis")
    print("Testando indices 0 a 4, aguarde...\n")
    for i in range(5):
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
        works = cap.isOpened()
        cap.release()
        status = "[OK] disponivel" if works else "[--] indisponivel"
        print(f"Indice {i}: {status}")
    print("\nDica: teste visualmente qual indice mostra sua webcam de verdade")
    print("usando a opcao de monitoramento e trocando o 'Indice da webcam' nas")
    print("Configuracoes ate achar o certo.")
    pause()


def toggle_flag(cfg, key, label):
    cfg[key] = not cfg[key]
    save_config(cfg)
    estado = "ATIVADO" if cfg[key] else "DESATIVADO"
    print(f"[OK] {label}: {estado}")
    log_event("CONFIGURACAO_ALTERADA", f"{key}={estado}")

    if key == "mic_mute_enabled" and cfg[key] and not PYCAW_AVAILABLE:
        print("[AVISO] pycaw nao esta instalado. Essa opcao nao vai funcionar")
        print("        ate voce rodar: pip install pycaw comtypes")
    pause()


def view_log():
    if not os.path.exists(LOG_PATH):
        header("Historico de eventos")
        print("Nenhum evento registrado ainda. Rode o monitoramento pelo menos")
        print("uma vez para comecar a gerar historico.")
        pause()
        return

    while True:
        header("Historico de eventos")
        print("1. Ver ultimas 30 entradas")
        print("2. Ver historico completo (paginado)")
        print("3. Limpar historico")
        print("0. Voltar ao menu principal")
        choice = input("\nEscolha uma opcao: ").strip()

        if choice == "0":
            return

        elif choice == "1":
            with open(LOG_PATH, "r", encoding="utf-8") as f:
                lines = f.readlines()
            header("Ultimas 30 entradas")
            if not lines:
                print("(historico vazio)")
            for line in lines[-30:]:
                print(line.rstrip())
            pause()

        elif choice == "2":
            with open(LOG_PATH, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if not lines:
                header("Historico completo")
                print("(historico vazio)")
                pause()
                continue
            page_size = 30
            for i in range(0, len(lines), page_size):
                header(f"Historico completo ({i + 1}-{min(i + page_size, len(lines))} de {len(lines)})")
                for line in lines[i:i + page_size]:
                    print(line.rstrip())
                if i + page_size < len(lines):
                    cont = input("\nENTER para ver mais, 'q' para parar: ").strip().lower()
                    if cont == "q":
                        break
                else:
                    pause()

        elif choice == "3":
            header("Limpar historico")
            confirm = input("Tem certeza que deseja apagar TODO o historico? (sim/nao): ").strip().lower()
            if confirm == "sim":
                try:
                    os.remove(LOG_PATH)
                    print("[OK] Historico apagado.")
                    log_event("HISTORICO_LIMPO")
                except OSError as e:
                    print(f"[ERRO] Nao consegui apagar o historico: {e}")
            else:
                print("Cancelado, nada foi apagado.")
            pause()

        else:
            print("[ERRO] Opcao invalida.")
            pause()


def show_help():
    header("Ajuda")
    print("Como funciona:")
    print(" - O app observa uma area da imagem (a porta) e compara com um")
    print("   'fundo' de referencia (porta fechada).")
    print(" - Quando detecta mudanca sustentada nessa area, considera que a")
    print("   porta abriu e substitui o video (e opcionalmente muta o mic).")
    print(" - Quando a area volta a ficar estavel, tudo volta ao normal.\n")
    print("No Discord/Meet/Zoom, selecione 'OBS Virtual Camera' como camera.")
    print("O OBS Studio precisa estar instalado (nao precisa estar aberto).\n")
    print("Se algo nao funcionar, confira as mensagens [ERRO] e [AVISO] que")
    print("aparecem durante a execucao - elas indicam o que fazer.")
    pause()


def main_menu():
    cfg = load_config()
    log_event("APP_INICIADO")
    while True:
        header("Menu Principal")
        print(f"1. Iniciar monitoramento")
        print(f"2. Configuracoes")
        print(f"3. Bloqueio de video ao abrir a porta: "
              f"{'ATIVADO' if cfg['video_block_enabled'] else 'DESATIVADO'}")
        print(f"4. Mudo automatico do microfone: "
              f"{'ATIVADO' if cfg['mic_mute_enabled'] else 'DESATIVADO'}")
        print(f"5. Testar cameras disponiveis")
        print(f"6. Ver historico de eventos")
        print(f"7. Ajuda")
        print(f"0. Sair")
        choice = input("\nEscolha uma opcao: ").strip()

        if choice == "1":
            run_monitor(cfg)
        elif choice == "2":
            settings_menu(cfg)
        elif choice == "3":
            header("Bloqueio de video")
            print("Quando ATIVADO: ao detectar a porta aberta, o video da camera")
            print("virtual (visto no Discord/Meet) e substituido por 'sem sinal'.")
            print("Quando DESATIVADO: a camera continua mostrando video normal")
            print("mesmo com a porta aberta (util se voce so quiser o mudo do mic).")
            toggle_flag(cfg, "video_block_enabled", "Bloqueio de video")
        elif choice == "4":
            header("Mudo automatico do microfone")
            print("Quando ATIVADO: ao detectar a porta aberta, o microfone padrao")
            print("do Windows e automaticamente mutado, voltando ao normal quando")
            print("a porta fechar. Exige a biblioteca 'pycaw' instalada.")
            toggle_flag(cfg, "mic_mute_enabled", "Mudo automatico do microfone")
        elif choice == "5":
            test_cameras()
        elif choice == "6":
            view_log()
        elif choice == "7":
            show_help()
        elif choice == "0":
            log_event("APP_ENCERRADO")
            print("Ate mais!")
            break
        else:
            print("[ERRO] Opcao invalida.")
            pause()


if __name__ == "__main__":
    try:
        main_menu()
    except KeyboardInterrupt:
        print("\n\nEncerrado pelo usuario.")
    except Exception as e:
        print(f"\n[ERRO FATAL] {e}")
        pause()