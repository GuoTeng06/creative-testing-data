"""MySQL TCP 代理 — Windows 侧运行，转发 WSL → MySQL"""
import socket
import threading
import sys

LISTEN_HOST = '0.0.0.0'
LISTEN_PORT = 3307
MYSQL_HOST = '192.168.16.38'
MYSQL_PORT = 3306

def handle(client_sock, addr):
    try:
        server_sock = socket.create_connection((MYSQL_HOST, MYSQL_PORT), timeout=10)
    except Exception as e:
        print(f"[ERR] 无法连接 MySQL: {e}")
        client_sock.close()
        return
    
    def forward(src, dst, name):
        try:
            while True:
                data = src.recv(8192)
                if not data:
                    break
                dst.sendall(data)
        except:
            pass
        finally:
            try: src.close()
            except: pass
            try: dst.close()
            except: pass
    
    t1 = threading.Thread(target=forward, args=(client_sock, server_sock, "C→M"))
    t2 = threading.Thread(target=forward, args=(server_sock, client_sock, "M→C"))
    t1.daemon = True; t2.daemon = True
    t1.start(); t2.start()
    t1.join(); t2.join()

def main():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((LISTEN_HOST, LISTEN_PORT))
    server.listen(5)
    print(f"MySQL 代理已启动: {LISTEN_HOST}:{LISTEN_PORT} → {MYSQL_HOST}:{MYSQL_PORT}")
    try:
        while True:
            client_sock, addr = server.accept()
            threading.Thread(target=handle, args=(client_sock, addr), daemon=True).start()
    except KeyboardInterrupt:
        print("\n代理已停止")
        server.close()

if __name__ == '__main__':
    main()
