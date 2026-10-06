#!/usr/bin/env python3
"""Read MDROBOT PID data without sending configuration or motor commands."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import time
from zoneinfo import ZoneInfo

import serial


def timestamp():
    return datetime.now(ZoneInfo('Asia/Seoul')).isoformat(timespec='milliseconds')


def request(device_id, tmid, target, index=None):
    if not 0 <= device_id <= 253 or not 0 <= target <= 253:
        raise ValueError('ID and requested PID must be 0..253; no broadcast')
    if tmid not in (172, 184):
        raise ValueError('Only documented PC/bridge TMIDs are allowed')
    if index is None:
        data = bytes((183, tmid, device_id, 4, 1, target))
    else:
        ranges = {225: range(1, 8), 226: range(1, 5), 227: range(1, 21)}
        if target not in ranges or index not in ranges[target]:
            raise ValueError('Invalid indexed read')
        data = bytes((183, tmid, device_id, 164, 2, target, index))
    return data + bytes(((-sum(data)) & 255,))


def find_frame(buffer, tmid, device_id, target):
    for offset in range(max(0, len(buffer) - 5)):
        if buffer[offset:offset + 4] != bytes((tmid, 183, device_id, target)):
            continue
        length = buffer[offset + 4] + 6
        frame = buffer[offset:offset + length]
        if len(frame) == length and sum(frame) & 255 == 0:
            return frame
    return None


class Reader:
    def __init__(self, port, log):
        self.log = log
        self.serial = serial.Serial(
            port=None, baudrate=115200, timeout=0.01,
            write_timeout=0.5, exclusive=True,
        )
        self.serial.dtr = False
        self.serial.rts = False
        self.serial.port = port
        self.serial.open()

    def close(self):
        self.serial.close()

    def query(self, baud, device_id, tmid, target, timeout, index=None):
        self.serial.baudrate = baud
        stale = self.serial.read(self.serial.in_waiting)
        tx = request(device_id, tmid, target, index)
        self.serial.write(tx)
        self.serial.flush()
        received = bytearray()
        deadline = time.monotonic() + timeout
        frame = None
        while time.monotonic() < deadline:
            received.extend(self.serial.read(max(1, self.serial.in_waiting)))
            frame = find_frame(received, tmid, device_id, target)
            if frame is not None:
                break
        result = {
            'timestamp': timestamp(), 'baudrate': baud,
            'device_id': device_id, 'tmid': tmid, 'pid': target,
            'index': index, 'tx_hex': tx.hex(' '),
            'rx_hex': bytes(received).hex(' '), 'stale_hex': stale.hex(' '),
            'status': 'ok' if frame is not None else (
                'no_valid_frame' if received else 'timeout'),
        }
        if frame is not None:
            payload = bytes(frame[5:-1])
            result.update(frame_hex=frame.hex(' '), data_hex=payload.hex(' '),
                          data_bytes=list(payload))
            if 0 < len(payload) <= 4:
                result['unsigned_le'] = int.from_bytes(payload, 'little')
                result['signed_le'] = int.from_bytes(payload, 'little', signed=True)
            if index is not None and (not payload or payload[0] != index):
                result['status'] = 'index_mismatch'
        self.log.write(json.dumps(result, ensure_ascii=False) + '\n')
        self.log.flush()
        return result


def discover(reader):
    for baud in (115200, 19200, 57600, 38400, 9600):
        print(f'Checking baudrate {baud}', flush=True)
        # Address 1 first, then all remaining unicast IDs. Never write an ID.
        for device_id in (1, *range(2, 254), 0):
            for tmid in (184, 172):
                item = reader.query(baud, device_id, tmid, 1, 0.08)
                if item['status'] == 'ok' and len(item['data_bytes']) == 1:
                    # Repeat the exact read to confirm the device before a dump.
                    second = reader.query(baud, device_id, tmid, 1, 0.4)
                    if second['status'] == 'ok' and second['data_hex'] == item['data_hex']:
                        print(json.dumps({'found': item}, ensure_ascii=False), flush=True)
                        return baud, device_id, tmid
        print(f'No verified version reply at {baud}', flush=True)
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--baud', type=int)
    parser.add_argument('--id', type=int, default=1)
    parser.add_argument('--tmid', type=int, default=184, choices=(172, 184))
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    metadata = {
        'started_at': timestamp(), 'port': args.port, 'motor_connected': False,
        'protocol': 'MDROBOT proprietary PID', 'serial_format': '8N1',
        'transmitted_pids': [4, 164], 'configuration_writes': False,
        'scope': 'PID 0..253 and indexed reads 225/226/227; not firmware flash',
    }
    with (output / 'serial_raw.jsonl').open('w') as log:
        reader = Reader(args.port, log)
        try:
            connection = (args.baud, args.id, args.tmid) if args.baud else discover(reader)
            if connection is None:
                metadata['status'] = 'no_device_response'
                (output / 'metadata.json').write_text(
                    json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
                print('No driver response. No baseline values collected.', flush=True)
                return 2
            baud, device_id, tmid = connection
            metadata.update(baudrate=baud, device_id=device_id, tmid=tmid)
            passes = []
            for pass_number in (1, 2):
                results = []
                for target in range(254):
                    item = reader.query(baud, device_id, tmid, target, 0.25)
                    results.append(item)
                    if item['status'] == 'ok':
                        print(f'Pass {pass_number}: PID {target}: {item["data_hex"]}', flush=True)
                for target, count in ((225, 7), (226, 4), (227, 20)):
                    for index in range(1, count + 1):
                        item = reader.query(baud, device_id, tmid, target, 0.25, index)
                        results.append(item)
                        if item['status'] == 'ok':
                            print(f'Pass {pass_number}: PID {target}[{index}]: '
                                  f'{item["data_hex"]}', flush=True)
                passes.append(results)
                (output / f'pass_{pass_number}.json').write_text(
                    json.dumps(results, ensure_ascii=False, indent=2) + '\n')
                print(f'Pass {pass_number} complete: '
                      f'{sum(r["status"] == "ok" for r in results)} valid replies', flush=True)
            metadata.update(status='complete', finished_at=timestamp())
            (output / 'metadata.json').write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
        finally:
            reader.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
