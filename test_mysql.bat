@echo off
py -3.14 -c "import pymysql; conn=pymysql.connect(host='192.168.16.38',user='root',password='root',database='creative testing data',charset='utf8mb4',connect_timeout=5); print('MySQL OK'); cur=conn.cursor(); cur.execute('SELECT COUNT(*) FROM `全部数据`'); print(cur.fetchone()[0]); conn.close()"
