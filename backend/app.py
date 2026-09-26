#!/usr/bin/env python3

from flask import Flask, jsonify, request
import subprocess
import os
import time
import shutil
import socket

app = Flask(__name__)

API_VERSION = "3.1"

# ============================================================
# AUTENTICACIÓN
# ============================================================

API_TOKEN = (
    os.environ.get("RASPBERRYPI_API_TOKEN")
    or os.environ.get("FALI_API_TOKEN")
)

def check_auth():
    auth = request.headers.get("Authorization", "")

    if auth.startswith("Bearer "):
        token = auth[7:]
        return token == API_TOKEN

    # Compatibilidad con configuración anterior
    legacy = request.headers.get("X-Fali-Token", "")
    if legacy and legacy == API_TOKEN:
        return True

    # Nuevo nombre
    raspberry_token = request.headers.get("X-RaspberryPi-Token", "")
    if raspberry_token and raspberry_token == API_TOKEN:
        return True

    return False


@app.before_request
def authentication():
    # Health no necesita token
    if request.path == "/api/health":
        return None

    if not check_auth():
        return jsonify({
            "ok": False,
            "error": "Unauthorized"
        }), 401


# ============================================================
# CONFIGURACIÓN
# ============================================================

MANAGED_SERVICES = {
    "radio_server": "radio-server.service",
    "radio_api": "radio-api.service",
    "radio_api_sync": "radio-api-sync.service",
    "music_downloader": "music-downloader.service",
    "senderismo_bot": "senderismo-bot.service",
    "antibot_telegram": "antibot-telegram.service",
    "samba": "smbd.service",
    "realvnc": "vncserver-x11-serviced.service",
    "wayvnc": "wayvnc.service",
}

PROTECTED_SERVICES = {
    "ssh": "ssh.service",
    "tailscaled": "tailscaled.service",
    "server_api": "server-api.service",
    "docker": "docker.service",
}

MANAGED_CONTAINERS = [
    "dispatcharr",
    "filebrowser",
    "syncthing",
    "navidrome",
]

PORTS = {
    "SSH": 22,
    "Samba": 445,
    "Navidrome": 4533,
    "RealVNC": 5900,
    "WayVNC": 5901,
    "Radio Server": 7755,
    "Radio API": 7756,
    "RaspberryPi API": 7777,
    "FileBrowser": 8085,
    "Syncthing": 8384,
    "Dispatcharr": 9191,
}


# ============================================================
# UTILIDADES
# ============================================================

def run_command(command, timeout=15):
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout
        )

        return {
            "returncode": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip()
        }

    except subprocess.TimeoutExpired:
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": "Command timeout"
        }

    except Exception as e:
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": str(e)
        }


def service_state(service):
    result = run_command([
        "systemctl",
        "is-active",
        service
    ])

    return result["stdout"] or "unknown"


def docker_state(container):
    result = run_command([
        "docker",
        "inspect",
        "-f",
        "{{.State.Status}}",
        container
    ])

    if result["returncode"] != 0:
        return "not_found"

    return result["stdout"] or "unknown"


def service_action(name, action):
    if name not in MANAGED_SERVICES:
        return {
            "ok": False,
            "error": "Service not allowed"
        }

    service = MANAGED_SERVICES[name]

    result = run_command([
        "sudo",
        "/usr/bin/systemctl",
        action,
        service
    ])

    if result["returncode"] != 0:
        return {
            "ok": False,
            "service": name,
            "action": action,
            "error": result["stderr"] or result["stdout"]
        }

    return {
        "ok": True,
        "service": name,
        "systemd_service": service,
        "action": action,
        "state": service_state(service)
    }


def docker_action(name, action):
    if name not in MANAGED_CONTAINERS:
        return {
            "ok": False,
            "error": "Container not allowed"
        }

    if action == "start":
        command = ["docker", "start", name]

    elif action == "stop":
        command = ["docker", "stop", name]

    elif action == "restart":
        command = ["docker", "restart", name]

    else:
        return {
            "ok": False,
            "error": "Invalid action"
        }

    result = run_command(command, timeout=60)

    if result["returncode"] != 0:
        return {
            "ok": False,
            "container": name,
            "action": action,
            "error": result["stderr"] or result["stdout"]
        }

    return {
        "ok": True,
        "container": name,
        "action": action,
        "state": docker_state(name)
    }


# ============================================================
# HEALTH
# ============================================================

@app.route("/api/health")
def health():

    return jsonify({
        "ok": True,
        "api": "RaspberryPi",
        "version": API_VERSION,
        "hostname": socket.gethostname(),
        "timestamp": int(time.time())
    })


# ============================================================
# STATUS GENERAL
# ============================================================

@app.route("/api/status")
def status():

    uptime = 0

    try:
        with open("/proc/uptime") as f:
            uptime = float(f.readline().split()[0])
    except Exception:
        pass

    load = os.getloadavg()

    return jsonify({
        "ok": True,
        "hostname": socket.gethostname(),
        "uptime_seconds": int(uptime),
        "load": {
            "1m": load[0],
            "5m": load[1],
            "15m": load[2]
        },
        "api_version": API_VERSION
    })


# ============================================================
# SISTEMA
# ============================================================

@app.route("/api/system")
def system():

    temperature = None

    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            temperature = round(int(f.read()) / 1000, 1)
    except Exception:
        pass

    memory = {}

    try:
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()

                if len(parts) >= 2:
                    key = parts[0].rstrip(":")
                    value = int(parts[1]) * 1024
                    memory[key] = value
    except Exception:
        pass

    total_ram = memory.get("MemTotal", 0)
    available_ram = memory.get("MemAvailable", 0)

    used_ram = total_ram - available_ram

    disk = shutil.disk_usage("/")

    return jsonify({
        "ok": True,
        "hostname": socket.gethostname(),
        "temperature_c": temperature,
        "cpu_count": os.cpu_count(),
        "memory": {
            "total": total_ram,
            "used": used_ram,
            "available": available_ram,
            "percent": round(
                (used_ram / total_ram) * 100, 1
            ) if total_ram else None
        },
        "disk": {
            "total": disk.total,
            "used": disk.used,
            "free": disk.free,
            "percent": round(
                (disk.used / disk.total) * 100, 1
            ) if disk.total else None
        }
    })


# ============================================================
@app.route("/api/system/reboot", methods=["POST"])
def system_reboot():
    result = subprocess.run(["sudo", "/usr/bin/systemctl", "reboot"], capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        return jsonify({"ok": False, "error": result.stderr or result.stdout}), 500
    return jsonify({"ok": True, "action": "reboot"})


@app.route("/api/system/shutdown", methods=["POST"])
def system_shutdown():
    result = subprocess.run(["sudo", "/usr/bin/systemctl", "poweroff"], capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        return jsonify({"ok": False, "error": result.stderr or result.stdout}), 500
    return jsonify({"ok": True, "action": "shutdown"})

# DASHBOARD
# ============================================================

@app.route("/api/dashboard")
def dashboard():

    services = {}

    for name, service in MANAGED_SERVICES.items():
        services[name] = service_state(service)

    containers = {}

    for name in MANAGED_CONTAINERS:
        containers[name] = docker_state(name)

    return jsonify({
        "ok": True,
        "services": services,
        "docker": containers
    })


# ============================================================
# DOCKER
# ============================================================

@app.route("/api/docker")
def docker():

    result = run_command([
        "docker",
        "ps",
        "-a",
        "--format",
        "{{.Names}}|{{.Status}}|{{.Ports}}"
    ])

    containers = []

    if result["returncode"] == 0:

        for line in result["stdout"].splitlines():

            parts = line.split("|", 2)

            if len(parts) >= 3:

                containers.append({
                    "name": parts[0],
                    "status": parts[1],
                    "ports": parts[2]
                })

    return jsonify({
        "ok": True,
        "containers": containers
    })


# ============================================================
# SERVICIOS
# ============================================================

@app.route("/api/services")
def services():

    result = []

    for name, service in MANAGED_SERVICES.items():

        result.append({
            "name": name,
            "service": service,
            "state": service_state(service),
            "managed": True
        })

    for name, service in PROTECTED_SERVICES.items():

        result.append({
            "name": name,
            "service": service,
            "state": service_state(service),
            "managed": False,
            "protected": True
        })

    return jsonify({
        "ok": True,
        "services": result
    })


# ============================================================
# CONTROL DE SERVICIOS
# ============================================================

@app.route("/api/services/<name>/<action>", methods=["POST"])
def control_service(name, action):

    if action not in ("start", "stop", "restart"):
        return jsonify({
            "ok": False,
            "error": "Invalid action"
        }), 400

    result = service_action(name, action)

    if not result["ok"]:
        return jsonify(result), 403

    return jsonify(result)


# ============================================================
# CONTROL DOCKER
# ============================================================

@app.route("/api/docker/<name>/<action>", methods=["POST"])
def control_docker(name, action):

    if action not in ("start", "stop", "restart"):
        return jsonify({
            "ok": False,
            "error": "Invalid action"
        }), 400

    result = docker_action(name, action)

    if not result["ok"]:
        return jsonify(result), 403

    return jsonify(result)


# ============================================================
# BOTS
# ============================================================

@app.route("/api/bots")
def bots():

    names = [
        "senderismo_bot",
        "antibot_telegram"
    ]

    result = {}

    for name in names:

        service = MANAGED_SERVICES[name]

        result[name] = {
            "service": service,
            "state": service_state(service)
        }

    return jsonify({
        "ok": True,
        "bots": result
    })


# ============================================================
# MÚSICA
# ============================================================

@app.route("/api/music")
def music():

    service = MANAGED_SERVICES["music_downloader"]

    return jsonify({
        "ok": True,
        "service": service,
        "state": service_state(service)
    })


# ============================================================
# RADIO
# ============================================================

@app.route("/api/radio")
def radio():

    return jsonify({
        "ok": True,
        "radio_server": {
            "service": "radio-server.service",
            "state": service_state("radio-server.service"),
            "port": 7755
        },
        "radio_api": {
            "service": "radio-api.service",
            "state": service_state("radio-api.service"),
            "port": 7756
        },
        "sync": {
            "service": "radio-api-sync.service",
            "state": service_state("radio-api-sync.service")
        }
    })


# ============================================================
# RED
# ============================================================

@app.route("/api/network")
def network():

    hostname = socket.gethostname()

    addresses = []

    result = run_command([
        "hostname",
        "-I"
    ])

    if result["returncode"] == 0:
        addresses = result["stdout"].split()

    interfaces = {}

    result = run_command([
        "ip",
        "-br",
        "addr"
    ])

    if result["returncode"] == 0:
        interfaces["summary"] = result["stdout"]

    tailscale = run_command([
        "tailscale",
        "ip"
    ])

    tailscale_ips = []

    if tailscale["returncode"] == 0:
        tailscale_ips = tailscale["stdout"].splitlines()

    return jsonify({
        "ok": True,
        "hostname": hostname,
        "addresses": addresses,
        "interfaces": interfaces,
        "tailscale": {
            "ips": tailscale_ips
        }
    })


# ============================================================
# ALMACENAMIENTO
# ============================================================

@app.route("/api/storage")
def storage():

    result = run_command([
        "df",
        "-h",
        "--output=target,size,used,avail,pcent"
    ])

    filesystems = []

    if result["returncode"] == 0:

        lines = result["stdout"].splitlines()

        for line in lines[1:]:

            parts = line.split()

            if len(parts) >= 5:

                filesystems.append({
                    "mount": parts[0],
                    "size": parts[1],
                    "used": parts[2],
                    "available": parts[3],
                    "percent": parts[4]
                })

    return jsonify({
        "ok": True,
        "filesystems": filesystems
    })


# ============================================================
# PUERTOS
# ============================================================

@app.route("/api/ports")
def ports():

    result = run_command([
        "ss",
        "-tuln"
    ])

    listening = []

    if result["returncode"] == 0:

        for line in result["stdout"].splitlines()[1:]:

            if line.strip():

                listening.append(line)

    return jsonify({
        "ok": True,
        "important_ports": PORTS,
        "listening": listening
    })



# ============================================================
# CONTROL WIFI
# ============================================================

def wifi_action(action):
    WIFI_INTERFACE = "wlan0"
    WIFI_CONNECTION = "Livebox6-836C"

    if action not in ("on", "off"):
        return {
            "ok": False,
            "error": "Invalid Wi-Fi action"
        }

    # ========================================================
    # WIFI ON
    # ========================================================

    if action == "on":

        # 1. Quitar bloqueo software de rfkill.
        unblock = run_command([
            "sudo", "-n",
            "/usr/sbin/rfkill",
            "unblock", "wifi"
        ])

        if unblock["returncode"] != 0:
            return {
                "ok": False,
                "target": "wifi",
                "interface": WIFI_INTERFACE,
                "action": action,
                "step": "rfkill_unblock",
                "error": unblock["stderr"] or unblock["stdout"]
            }

        # 2. Activar la radio Wi-Fi.
        radio = run_command([
            "sudo", "-n",
            "/usr/bin/nmcli",
            "radio", "wifi", "on"
        ])

        if radio["returncode"] != 0:
            return {
                "ok": False,
                "target": "wifi",
                "interface": WIFI_INTERFACE,
                "action": action,
                "step": "radio_on",
                "error": radio["stderr"] or radio["stdout"]
            }

        # 3. Activar el perfil guardado de Livebox.
        result = run_command([
            "sudo", "-n",
            "/usr/bin/nmcli",
            "connection", "up",
            WIFI_CONNECTION
        ], timeout=30)

        if result["returncode"] != 0:
            return {
                "ok": False,
                "target": "wifi",
                "interface": WIFI_INTERFACE,
                "connection": WIFI_CONNECTION,
                "action": action,
                "step": "connection_up",
                "error": result["stderr"] or result["stdout"]
            }

    # ========================================================
    # WIFI OFF
    # ========================================================

    else:

        # 1. Desconectar el perfil Wi-Fi.
        down = run_command([
            "sudo", "-n",
            "/usr/bin/nmcli",
            "connection", "down",
            WIFI_CONNECTION
        ])

        # Si ya estaba desconectado, intentar desconectar wlan0.
        if down["returncode"] != 0:
            disconnect = run_command([
                "sudo", "-n",
                "/usr/bin/nmcli",
                "device", "disconnect",
                WIFI_INTERFACE
            ])

            if disconnect["returncode"] != 0:
                return {
                    "ok": False,
                    "target": "wifi",
                    "interface": WIFI_INTERFACE,
                    "action": action,
                    "step": "disconnect",
                    "error": disconnect["stderr"] or disconnect["stdout"]
                }

        # 2. Apagar la radio Wi-Fi.
        radio = run_command([
            "sudo", "-n",
            "/usr/bin/nmcli",
            "radio", "wifi", "off"
        ])

        if radio["returncode"] != 0:
            return {
                "ok": False,
                "target": "wifi",
                "interface": WIFI_INTERFACE,
                "action": action,
                "step": "radio_off",
                "error": radio["stderr"] or radio["stdout"]
            }

        # 3. Bloquear el adaptador mediante rfkill.
        block = run_command([
            "sudo", "-n",
            "/usr/sbin/rfkill",
            "block", "wifi"
        ])

        if block["returncode"] != 0:
            return {
                "ok": False,
                "target": "wifi",
                "interface": WIFI_INTERFACE,
                "action": action,
                "step": "rfkill_block",
                "error": block["stderr"] or block["stdout"]
            }

    # ========================================================
    # ESTADO FINAL
    # ========================================================

    state = run_command([
        "/usr/bin/nmcli",
        "-t",
        "-f",
        "DEVICE,STATE,CONNECTION",
        "device"
    ])

    wifi_state = "unknown"
    wifi_connection = None

    if state["returncode"] == 0:
        for line in state["stdout"].splitlines():
            if line.startswith(WIFI_INTERFACE + ":"):
                parts = line.split(":", 2)

                if len(parts) >= 2:
                    wifi_state = parts[1]

                if len(parts) >= 3:
                    wifi_connection = parts[2] or None

                break

    radio_state = run_command([
        "/usr/bin/nmcli",
        "radio",
        "wifi"
    ])

    rfkill_state = run_command([
        "/usr/sbin/rfkill",
        "list",
        "wifi"
    ])

    return {
        "ok": True,
        "target": "wifi",
        "interface": WIFI_INTERFACE,
        "connection": wifi_connection,
        "configured_connection": WIFI_CONNECTION,
        "action": action,
        "state": wifi_state,
        "radio": radio_state["stdout"] or "unknown",
        "rfkill": rfkill_state["stdout"].strip()
    }


# ============================================================
# CONTROL DISPONIBLE
# ============================================================

@app.route("/api/control", methods=["GET", "POST"])
def control():

    # ========================================================
    # GET -> acciones disponibles
    # ========================================================

    if request.method == "GET":
        return jsonify({
            "ok": True,
            "api": "RaspberryPi",
            "version": API_VERSION,

            "wifi": {
                "interface": "wlan0",
                "actions": ["on", "off"]
            },

            "services": {
                name: {
                    "service": service,
                    "actions": ["start", "stop", "restart"]
                }
                for name, service in MANAGED_SERVICES.items()
            },

            "docker": {
                name: {
                    "actions": ["start", "stop", "restart"]
                }
                for name in MANAGED_CONTAINERS
            },

            "protected_services": {
                name: service
                for name, service in PROTECTED_SERVICES.items()
            },

            "diagnostics": [
                "/api/diagnostics",
                "/api/diagnostics/network",
                "/api/diagnostics/wifi",
                "/api/diagnostics/events"
            ]
        })


    # ========================================================
    # POST -> ejecutar acción
    # ========================================================

    data = request.get_json(silent=True) or {}

    target = data.get("target")
    name = data.get("name")
    action = data.get("action")


    # --------------------------------------------------------
    # WIFI
    # --------------------------------------------------------

    if target == "wifi":

        if action not in ("on", "off"):
            return jsonify({
                "ok": False,
                "error": "Invalid Wi-Fi action. Use on or off."
            }), 400

        return jsonify(
            wifi_action(action)
        )


    # --------------------------------------------------------
    # SERVICE
    # --------------------------------------------------------

    if target == "service":

        if action not in ("start", "stop", "restart"):
            return jsonify({
                "ok": False,
                "error": "Invalid service action"
            }), 400

        if name in PROTECTED_SERVICES:
            return jsonify({
                "ok": False,
                "error": "Protected service cannot be controlled",
                "service": name
            }), 403

        result = service_action(name, action)

        if not result["ok"]:
            return jsonify(result), 403

        return jsonify(result)


    # --------------------------------------------------------
    # DOCKER
    # --------------------------------------------------------

    if target == "docker":

        if action not in ("start", "stop", "restart"):
            return jsonify({
                "ok": False,
                "error": "Invalid Docker action"
            }), 400

        result = docker_action(name, action)

        if not result["ok"]:
            return jsonify(result), 403

        return jsonify(result)


    # --------------------------------------------------------
    # INVALID TARGET
    # --------------------------------------------------------

    return jsonify({
        "ok": False,
        "error": "Invalid target",
        "allowed_targets": [
            "wifi",
            "service",
            "docker"
        ]
    }), 400


# ============================================================
# RAÍZ
# ============================================================

@app.route("/")
def index():

    return jsonify({
        "name": "RaspberryPi",
        "api": API_VERSION,
        "status": "online",
        "authentication": "Bearer token",
        "health": "/api/health"
    })


# ============================================================
# ARRANQUE
# ============================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=7777,
        debug=False
    )
