"""
RTSP pre-check: says *why* a stream cannot be opened before OpenCV tries it.

OpenCV/FFmpeg only report "could not open". This module talks RTSP directly, with at most one
request without credentials and - only if the device asks for a login and credentials are in
the URL - one request with them (Digest or Basic). That classifies the common failures:
network unreachable, timeout, login required, credentials rejected, no permission, and wrong
channel path. A single login attempt per check keeps clear of device lockout rules (Hikvision
locks an address out after several failed logins).

No credential is ever returned, logged or included in a message; hosts and paths are fine.
"""
import base64
import hashlib
import os
import re
import socket
from dataclasses import dataclass
from typing import Dict, Optional, Tuple
from urllib.parse import unquote

# Authority: optional user:password (up to the LAST "@"), then host[:port]
_RTSP_URL = re.compile(r"^(?P<scheme>rtsps?)://(?:(?P<userinfo>[^/\s]*)@)?(?P<host>[^/:\s?#]+)(?::(?P<port>\d+))?"
                       r"(?P<path>[^\s]*)$", re.IGNORECASE)

LOCKOUT_NOTE = ("Hikvision and similar devices lock out an address after several failed logins, so check the "
                "account with the device administrator before trying again.")


@dataclass
class RtspCheck:
    ok: Optional[bool]   # True = stream exists and the login works; False = will not open; None = not checked
    category: str        # ok, unreachable, timeout, auth_required, auth_failed, forbidden, not_found, protocol, bad_url, not_checked
    detail: str
    status: Optional[int] = None


def parse(url: str) -> Optional[Dict[str, str]]:
    m = _RTSP_URL.match(url.strip())
    if not m:
        return None
    user = password = None
    if m.group("userinfo") is not None:
        userinfo = m.group("userinfo")
        user, _, password = userinfo.partition(":")
        user, password = unquote(user), unquote(password)
    port = int(m.group("port") or 554)
    path = m.group("path") or "/"
    return {"scheme": m.group("scheme").lower(), "host": m.group("host"), "port": port, "path": path,
            "user": user, "password": password,
            # Request-URI without credentials
            "uri": f"{m.group('scheme').lower()}://{m.group('host')}:{port}{path}"}


def _read_response(sock: socket.socket) -> Tuple[int, str, Dict[str, str]]:
    data = b""
    while b"\r\n\r\n" not in data and len(data) < 65536:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    head = data.split(b"\r\n\r\n", 1)[0].decode("latin-1", "replace")
    lines = head.split("\r\n")
    status_line = lines[0] if lines else ""
    m = re.match(r"RTSP/\d\.\d\s+(\d{3})", status_line)
    if not m:
        raise ValueError(status_line[:80] or "empty reply")
    headers: Dict[str, str] = {}
    for line in lines[1:]:
        name, _, value = line.partition(":")
        key = name.strip().lower()
        if key == "www-authenticate" and key in headers:
            # Several challenges: keep Digest if offered
            if value.strip().lower().startswith("digest"):
                headers[key] = value.strip()
            continue
        headers[key] = value.strip()
    return int(m.group(1)), status_line, headers


def _authorization(challenge: str, user: str, password: str, uri: str) -> Optional[str]:
    scheme = challenge.split(" ", 1)[0].lower()
    if scheme == "basic":
        return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
    if scheme != "digest":
        return None
    params = dict(re.findall(r'(\w+)="?([^",]*)"?', challenge[len("digest"):]))
    realm, nonce = params.get("realm", ""), params.get("nonce", "")
    ha1 = hashlib.md5(f"{user}:{realm}:{password}".encode()).hexdigest()
    ha2 = hashlib.md5(f"DESCRIBE:{uri}".encode()).hexdigest()
    parts = [f'username="{user}"', f'realm="{realm}"', f'nonce="{nonce}"', f'uri="{uri}"']
    qop = params.get("qop", "")
    if "auth" in [q.strip() for q in qop.split(",")]:
        cnonce = os.urandom(8).hex()
        response = hashlib.md5(f"{ha1}:{nonce}:00000001:{cnonce}:auth:{ha2}".encode()).hexdigest()
        parts += ['qop=auth', 'nc=00000001', f'cnonce="{cnonce}"']
    else:
        response = hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()
    parts.append(f'response="{response}"')
    if params.get("opaque"):
        parts.append(f'opaque="{params["opaque"]}"')
    return "Digest " + ", ".join(parts)


def _describe(sock: socket.socket, uri: str, cseq: int, authorization: Optional[str] = None):
    request = (f"DESCRIBE {uri} RTSP/1.0\r\nCSeq: {cseq}\r\nUser-Agent: AttendAI\r\nAccept: application/sdp\r\n"
               + (f"Authorization: {authorization}\r\n" if authorization else "") + "\r\n")
    sock.sendall(request.encode())
    return _read_response(sock)


def _result_for(status: int, where: str) -> RtspCheck:
    if status == 200:
        return RtspCheck(True, "ok", f"{where}: the device accepted the login and has a stream at this path.", status)
    if status == 401:
        return RtspCheck(False, "auth_failed", f"{where}: the device rejected the username or password (RTSP 401). "
                                               + LOCKOUT_NOTE, status)
    if status == 403:
        return RtspCheck(False, "forbidden", f"{where}: this account may not view the stream (RTSP 403). Ask the "
                                             "administrator to give it Live View permission for this channel.", status)
    if status in (404, 454):
        return RtspCheck(False, "not_found", f"{where}: the device has no stream at this path (RTSP {status}). Check "
                                             "the channel: on Hikvision /Streaming/Channels/<channel>01 is the main "
                                             "stream and <channel>02 the sub-stream (channel 1 = 101/102).", status)
    return RtspCheck(False, "protocol", f"{where}: unexpected RTSP reply {status}.", status)


def check(url: str, timeout: float = 5.0) -> RtspCheck:
    """Classify whether an rtsp:// stream will open. Never raises for network problems."""
    parts = parse(url or "")
    if parts is None:
        return RtspCheck(False, "bad_url", "The stream URL is not a valid rtsp://host[:port]/path address.")
    where = f"{parts['host']}:{parts['port']}{parts['path']}"
    if parts["scheme"] == "rtsps":
        return RtspCheck(None, "not_checked", f"{where}: encrypted RTSP is not pre-checked; opening it directly.")
    try:
        sock = socket.create_connection((parts["host"], parts["port"]), timeout=timeout)
    except socket.timeout:
        return RtspCheck(False, "unreachable", f"Cannot reach {parts['host']}:{parts['port']} within {timeout:.0f} s "
                                               "(network, firewall, or the device is off).")
    except OSError:
        return RtspCheck(False, "unreachable", f"Cannot connect to {parts['host']}:{parts['port']} (network, firewall, "
                                               "or the device is off).")
    try:
        sock.settimeout(timeout)
        status, _, headers = _describe(sock, parts["uri"], 1)
        if status != 401:
            return _result_for(status, where)
        if not parts["user"] or parts["password"] is None:
            return RtspCheck(False, "auth_required", f"{where}: the device requires a login. Add the camera account "
                                                     "to the stream URL (rtsp://<user>:<password>@host/...).", 401)
        authorization = _authorization(headers.get("www-authenticate", ""), parts["user"], parts["password"],
                                       parts["uri"])
        if authorization is None:
            return RtspCheck(False, "protocol", f"{where}: the device asks for an unsupported login method.", 401)
        status, _, _ = _describe(sock, parts["uri"], 2, authorization)
        return _result_for(status, where)
    except socket.timeout:
        return RtspCheck(False, "timeout", f"{where}: connected, but the device did not answer the RTSP request "
                                           f"within {timeout:.0f} s.")
    except (OSError, ValueError) as e:
        return RtspCheck(False, "protocol", f"{where}: the device did not reply as an RTSP server "
                                            f"({type(e).__name__}).")
    finally:
        sock.close()
