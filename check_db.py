import psycopg2
conn = psycopg2.connect(host='localhost',port=5432,dbname='ooredoo_sales',user='postgres',password='admin')
conn.set_client_encoding('UTF8')
cur = conn.cursor()

cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_schema='public' AND table_name='stock' ORDER BY ordinal_position")
print('=== stock public ===')
for r in cur.fetchall(): print(f'  {r[0]}: {r[1]}')

cur.execute("SELECT MAX(ratio_eod), MIN(ratio_eod) FROM public.ratios_historiques")
print('ratios_historiques max/min:', cur.fetchone())

cur.execute("SELECT DISTINCT actif FROM public.produits LIMIT 5")
print('produits actif values:', cur.fetchall())

cur.execute("SELECT COUNT(*) FROM public.stock")
print('stock rows:', cur.fetchone()[0])

conn.close()
