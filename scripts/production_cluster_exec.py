"""Bounded TLS-verified transport used only for the fixed runtime log reader."""
import base64
import hashlib
import json
import os
import socket
import struct
import urllib.parse


def audit_exec(context, node, source):
    if not __import__('re').fullmatch(r'adx-node-[0-9]+', node):
        raise ValueError('invalid audit node')
    query=urllib.parse.urlencode([('container','runtime'),('stdout','true'),('stderr','true'),
                                  ('command','python3'),('command','-c'),('command',source)])
    path='/api/v1/namespaces/adx-jiuwen/pods/'+node+'/exec?'+query
    key=base64.b64encode(os.urandom(16)).decode()
    with socket.create_connection(('192.168.7.18',5443),timeout=30) as raw:
      with context.wrap_socket(raw,server_hostname='192.168.7.18') as sock:
        sock.sendall(('GET '+path+' HTTP/1.1\r\nHost: 192.168.7.18:5443\r\nConnection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: '+key+'\r\nSec-WebSocket-Protocol: v4.channel.k8s.io\r\n\r\n').encode())
        buf=b''
        while b'\r\n\r\n' not in buf:
            chunk=sock.recv(4096)
            if not chunk:raise EOFError('truncated exec handshake')
            buf+=chunk
            if len(buf)>65536:raise ValueError('oversized handshake')
        head,buf=buf.split(b'\r\n\r\n',1)
        if b' 101 ' not in head.split(b'\r\n',1)[0]:raise RuntimeError('audit exec upgrade denied')
        headers={k.lower().strip():v.strip() for line in head.split(b'\r\n')[1:] if b':' in line for k,v in [line.split(b':',1)]}
        expected=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest())
        if headers.get(b'sec-websocket-accept')!=expected:raise RuntimeError('invalid upgrade')
        def read(n):
            nonlocal buf
            while len(buf)<n:
                chunk=sock.recv(min(65536,n-len(buf)))
                if not chunk:raise EOFError('truncated exec stream')
                buf+=chunk
            value,buf=buf[:n],buf[n:]
            return value
        if headers.get(b'sec-websocket-protocol') != b'v4.channel.k8s.io':
            raise RuntimeError('unexpected exec protocol')
        stdout=bytearray();status=None;partial=bytearray()
        while True:
            a,b=read(2);opcode=a&15;length=b&127
            if length==126:length=struct.unpack('!H',read(2))[0]
            elif length==127:length=struct.unpack('!Q',read(8))[0]
            if b&128 or length>16_000_000:raise ValueError('invalid exec frame')
            payload=read(length)
            if opcode==8:break
            if opcode==9:
                mask=os.urandom(4);sock.sendall(bytes([0x8a,0x80|len(payload)])+mask+bytes(x^mask[i%4] for i,x in enumerate(payload)));continue
            if opcode not in (0,1,2):continue
            partial.extend(payload)
            if len(partial)>16_000_000:raise ValueError('fragmented exec output exceeded bound')
            if not a&128:continue
            payload=bytes(partial);partial.clear()
            if not payload:continue
            if payload[0]==1:stdout.extend(payload[1:])
            elif payload[0]==3:status=json.loads(payload[1:]);break
            if len(stdout)>16_000_000:raise ValueError('audit output exceeded bound')
        if status is None or status.get('status')!='Success':raise RuntimeError('audit exec failed')
        return json.loads(stdout)
