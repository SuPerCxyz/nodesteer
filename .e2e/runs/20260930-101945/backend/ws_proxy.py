#!/usr/bin/env python3
"""本轮透明WS帧代理：真实Hub/Agent数据透传，仅扰动指定小型控制帧。"""
import argparse
import asyncio
import json
from pathlib import Path
import struct
import time

CONTROL_TYPES = {'CHANGE_NOTIFICATION', 'EXECUTION_ACK', 'SYNC_RESPONSE', 'REMOTE_STATE', 'RUN_EXECUTION'}


def encode(payload, opcode=1, mask=None):
    first = bytes([0x80 | opcode])
    size = len(payload)
    mask_bit = 0x80 if mask else 0
    header = bytes([mask_bit | size]) if size < 126 else bytes([mask_bit | 126]) + struct.pack('!H', size) if size < 65536 else bytes([mask_bit | 127]) + struct.pack('!Q', size)
    if mask:
        payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    return first + header + (mask or b'') + payload


async def frame(reader):
    header = await reader.readexactly(2)
    first, second = header
    extended = b''
    size = second & 0x7f
    if size == 126:
        extended = await reader.readexactly(2); size = struct.unpack('!H', extended)[0]
    elif size == 127:
        extended = await reader.readexactly(8); size = struct.unpack('!Q', extended)[0]
    if size > 16 * 1048576: raise ValueError('frame exceeds run limit')
    mask = await reader.readexactly(4) if second & 0x80 else b''
    payload = await reader.readexactly(size)
    decoded = bytes(value ^ mask[index % 4] for index, value in enumerate(payload)) if mask else payload
    return header + extended + mask + payload, first, mask, decoded


class Proxy:
    def __init__(self, controls, events, upstream_host, upstream_port):
        self.controls = controls
        self.events = events
        self.upstream_host, self.upstream_port = upstream_host, upstream_port

    def log(self, **entry):
        with self.events.open('a') as stream:
            stream.write(json.dumps({'time': time.time(), **entry}) + '\n')

    def settings(self):
        try: return json.loads(self.controls.read_text())
        except (OSError, ValueError): return {}

    async def connection(self, client_reader, client_writer):
        upstream_writer = None
        connection_id = str(time.time_ns())
        try:
            header = await asyncio.wait_for(client_reader.readuntil(b'\r\n\r\n'), 10)
            upstream_reader, upstream_writer = await asyncio.open_connection(self.upstream_host, self.upstream_port)
            upstream_writer.write(header); await upstream_writer.drain()
            response = await asyncio.wait_for(upstream_reader.readuntil(b'\r\n\r\n'), 10)
            client_writer.write(response); await client_writer.drain()
            websocket = b'upgrade: websocket' in header.lower() and b' 101 ' in response.split(b'\r\n', 1)[0]
            self.log(event='connection', connection=connection_id, websocket=websocket)
            fault = self.settings().get('http_fault', {})
            request_path = header.split(b'\r\n', 1)[0].split()[1].decode()
            if not websocket and request_path == fault.get('path') and fault.get('mode') in {'truncate', 'corrupt'}:
                size = next((int(line.split(b':', 1)[1]) for line in response.split(b'\r\n') if line.lower().startswith(b'content-length:')), 0)
                if not 0 < size <= 4 * 1048576: raise ValueError('HTTP fault payload exceeds test limit')
                count = min(size, 128) if fault['mode'] == 'truncate' else size
                body = await upstream_reader.readexactly(count)
                if fault['mode'] == 'corrupt': body = bytes([body[0] ^ 1]) + body[1:]
                client_writer.write(body); await client_writer.drain()
                self.log(event='http_fault', connection=connection_id, mode=fault['mode'], path=request_path, declared_bytes=size, sent_bytes=len(body))
                return
            held, reordered, replays = [], [], []

            async def pipe(reader, writer, direction):
                while True:
                    if not websocket:
                        data = await reader.read(65536)
                        if not data: return
                        writer.write(data); await writer.drain(); continue
                    raw, first, mask, payload = await frame(reader)
                    message = {}
                    if first & 0x80 and not first & 0x70 and first & 0x0f in {1, 2} and len(payload) <= 1048576:
                        try: message = json.loads(payload)
                        except (ValueError, UnicodeDecodeError): pass
                    if not isinstance(message, dict): message = {}
                    kind = message.get('type', '')
                    controls = self.settings()
                    if direction == 'down' and kind in CONTROL_TYPES:
                        fields = {'connection': connection_id, 'direction': direction, 'type': kind, 'id': message.get('id', '')}
                        if kind == 'SYNC_RESPONSE':
                            fields.update(global_rev=message.get('payload', {}).get('global_rev'), full_resync=message.get('payload', {}).get('full_resync', False), snapshot=message.get('payload', {}).get('snapshot', False))
                        if kind in controls.get('drop_types', []):
                            self.log(event='drop', **fields); continue
                        if kind in controls.get('hold_types', []):
                            held.append((writer, raw, kind)); self.log(event='hold', **fields); continue
                        if kind in controls.get('reorder_types', []):
                            reordered.append(raw)
                            if len(reordered) < 2:
                                self.log(event='reorder_hold', **fields); continue
                            writer.write(reordered.pop()); writer.write(reordered.pop()); await writer.drain()
                            self.log(event='reorder_release', **fields); continue
                        if kind == 'SYNC_RESPONSE' and controls.get('corrupt_script_id'):
                            target = controls['corrupt_script_id']
                            for script in message.get('payload', {}).get('scripts', []):
                                if script.get('id') == target:
                                    script['data'] = 'qa-invalid-script-dependency'
                                    raw = encode(json.dumps(message).encode(), first & 0x0f, mask)
                                    self.log(event='corrupt_script', target_id=target, **fields)
                        if kind == 'SYNC_RESPONSE' and controls.get('corrupt_script_sha'):
                            target = controls['corrupt_script_sha']
                            for script in message.get('payload', {}).get('scripts', []):
                                if script.get('id') == target and isinstance(script.get('data'), dict):
                                    script['data']['content'] = 'printf injected-script-corruption'
                                    raw = encode(json.dumps(message).encode(), first & 0x0f, mask)
                                    self.log(event='corrupt_script_sha', target_id=target, **fields)
                        if kind == 'SYNC_RESPONSE' and controls.get('redirect_app_id'):
                            target = controls['redirect_app_id']
                            for app in message.get('payload', {}).get('apps', []):
                                if app.get('id') == target and isinstance(app.get('data'), dict):
                                    app['data']['artifact_url'] = controls['artifact_proxy_url']
                                    raw = encode(json.dumps(message).encode(), first & 0x0f, mask)
                                    self.log(event='redirect_app_artifact', target_id=target, **fields)
                        if kind == 'REMOTE_STATE' and controls.get('expire_remote'):
                            message['payload']['observed_at'] = '2000-01-01T00:00:00Z'
                            message['payload']['ttl_sec'] = 1
                            raw = encode(json.dumps(message).encode(), first & 0x0f, mask)
                            self.log(event='expire_remote', property=message['payload'].get('property'), **fields)
                        writer.write(raw)
                        duplicate_execution = kind == 'RUN_EXECUTION' and message.get('payload', {}).get('task_id') == controls.get('duplicate_task_id')
                        if kind in controls.get('duplicate_types', []) or duplicate_execution:
                            writer.write(raw); self.log(event='duplicate', **fields)
                        else: self.log(event='forward_control', **fields)
                        if duplicate_execution:
                            replays.append((time.monotonic() + controls.get('replay_delay', 4), writer, raw, fields))
                    else:
                        writer.write(raw)
                        if first & 0x70 or not first & 0x80:
                            self.log(event='opaque_passthrough', connection=connection_id, direction=direction, rsv=first & 0x70, fragmented=not bool(first & 0x80))
                    await writer.drain()

            async def release():
                while True:
                    await asyncio.sleep(.1)
                    settings = self.settings()
                    if settings.get('disconnect_connection') == connection_id:
                        self.log(event='injected_disconnect', connection=connection_id)
                        return
                    for item in list(held):
                        writer, raw, kind = item
                        if kind not in settings.get('hold_types', []):
                            writer.write(raw); await writer.drain(); held.remove(item)
                            self.log(event='release_held', connection=connection_id, type=kind)
                    for item in list(replays):
                        due, writer, raw, fields = item
                        if time.monotonic() >= due:
                            writer.write(raw); await writer.drain(); replays.remove(item)
                            self.log(event='replay_execution', **fields)

            tasks = [asyncio.create_task(pipe(client_reader, upstream_writer, 'up')), asyncio.create_task(pipe(upstream_reader, client_writer, 'down')), asyncio.create_task(release())]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending: task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as exc:
            self.log(event='connection_closed', connection=connection_id, error_type=type(exc).__name__)
        finally:
            client_writer.close()
            if upstream_writer: upstream_writer.close()


async def self_check():
    for size in [2, 125, 126, 65536]:
        for mask in [None, b'1234']:
            data = b'x' * size
            reader = asyncio.StreamReader(); encoded = encode(data, mask=mask)
            reader.feed_data(encoded); reader.feed_eof()
            raw, first, actual_mask, decoded = await frame(reader)
            assert raw == encoded and decoded == data and bool(actual_mask) == bool(mask)
    print('WS mask/length/roundtrip self-check passed; no network started')


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--listen', type=int, default=18084)
    parser.add_argument('--upstream-host', default='127.0.0.1')
    parser.add_argument('--upstream-port', type=int, default=18080)
    parser.add_argument('--controls', type=Path)
    parser.add_argument('--events', type=Path)
    parser.add_argument('--self-check', action='store_true')
    args = parser.parse_args()
    if args.self_check: await self_check(); return
    assert args.controls and args.events
    proxy = Proxy(args.controls, args.events, args.upstream_host, args.upstream_port)
    server = await asyncio.start_server(proxy.connection, '0.0.0.0', args.listen)
    proxy.log(event='proxy_started', port=args.listen)
    async with server: await server.serve_forever()


if __name__ == '__main__':
    asyncio.run(main())
