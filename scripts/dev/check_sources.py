import sqlite3
conn = sqlite3.connect('data/botkin.db')
cursor = conn.cursor()
cursor.execute("SELECT source_type, instruction_url, trade_name FROM drug_instructions WHERE source_type='searxng' AND instruction_url IS NOT NULL LIMIT 5")
for row in cursor.fetchall():
    print(f"{row[0]} | {row[1][:60]}... | {row[2]}")
