from ldap3 import Server, Connection, ALL
import psycopg2
from dotenv import load_dotenv
import os
import logging

# -------------------------
# LOGGING // Логирование
# -------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("sync.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# -------------------------
# LOAD .env // Загрузка переменных
# -------------------------

load_dotenv()

DOMEN = os.getenv("DOMEN")
SERVER_AD = os.getenv("SERVER_AD")
ADMIN_LOGIN = os.getenv("ADMIN_LOGIN")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

POSTGRES_DB = os.getenv("POSTGRES_DB")
POSTGRES_USER = os.getenv("POSTGRES_USER")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
POSTGRES_HOST = os.getenv("POSTGRES_HOST")
POSTGRES_PORT = os.getenv("POSTGRES_PORT")

# -------------------------
# CONNECT TO ACTIVE DIRECTORY // Подключение к Контроллеру доменов
# -------------------------

logger.info("Подключение к Active Directory...")

server = Server(SERVER_AD, get_info=ALL)

conn = Connection(
    server,
    user=ADMIN_LOGIN,
    password=ADMIN_PASSWORD,
    auto_bind=True
)

logger.info("AD подключен")

# -------------------------
# CONNECT TO POSTGRESQL // Подключение к Postgresql развернутый в докере
# -------------------------

logger.info("Подключение к PostgreSQL...")

db = psycopg2.connect(
    host=POSTGRES_HOST,
    database=POSTGRES_DB,
    user=POSTGRES_USER,
    password=POSTGRES_PASSWORD,
    port=POSTGRES_PORT
)

cursor = db.cursor()

logger.info("PostgreSQL подключен")

# -------------------------
# BASE DN
# -------------------------

base_dn = f"dc={DOMEN.split('.')[0]},dc={DOMEN.split('.')[1]}"

# -------------------------
# GET USERS FROM AD // Получаем пользователей из AD
# -------------------------

logger.info("Получение пользователей из AD...")

conn.search(
    search_base=base_dn,
    search_filter='(&(objectClass=user)(userPrincipalName=*))',
    attributes=[
        'userPrincipalName',
        'sAMAccountName',
        'givenName',
        'sn',
        'lastLogonTimestamp'
    ]
)

users = conn.entries
logger.info(f"Найдено пользователей: {len(users)}")

# -------------------------
# INSERT USERS // Вставка в БД
# -------------------------

for u in users:
    upn = str(u.userPrincipalName)
    sam = str(u.sAMAccountName)
    first = str(u.givenName)
    last = str(u.sn)
    last_logon = u.lastLogonTimestamp.value if u.lastLogonTimestamp else None

    cursor.execute("""
        INSERT INTO Users (UPN, SAMAccountName, FirstName, LastName, LastLogon)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (UPN) DO UPDATE SET
            SAMAccountName = EXCLUDED.SAMAccountName,
            FirstName = EXCLUDED.FirstName,
            LastName = EXCLUDED.LastName,
            LastLogon = EXCLUDED.LastLogon
    """, (upn, sam, first, last, last_logon))
logger.info("Users синхронизированы")

# -------------------------
# DELETE USERS NOT IN AD //Удаляем пользователей которых нет в AD
# -------------------------

ad_upns = [str(u.userPrincipalName) for u in users]

cursor.execute("SELECT UPN FROM Users")
db_upns = [row[0] for row in cursor.fetchall()]

deleted_users = 0

for upn in db_upns:
    if upn not in ad_upns:
        cursor.execute("DELETE FROM Users WHERE UPN=%s", (upn,))
        deleted_users += 1

logger.info(f"Удалено пользователей: {deleted_users}")

# -------------------------
# GET GROUPS FROM AD // Получаем группы из AD
# -------------------------

logger.info("Получение групп из AD...")

conn.search(
    search_base=base_dn,
    search_filter='(objectClass=group)',
    attributes=['cn']
)

groups = conn.entries
logger.info(f"Найдено групп: {len(groups)}")

# -------------------------
# INSERT GROUPS // Вставка групп в БД
# -------------------------

for g in groups:
    name = str(g.cn)

    cursor.execute("""
        INSERT INTO Groups (Name)
        VALUES (%s)
        ON CONFLICT (Name) DO NOTHING
    """, (name,))

logger.info("Groups синхронизированы")

# -------------------------
# DELETE GROUPS NOT IN AD // Удаление групп которых нет в AD
# -------------------------

ad_groups = [str(g.cn) for g in groups]

cursor.execute("SELECT Name FROM Groups")
db_groups = [row[0] for row in cursor.fetchall()]

deleted_groups = 0

for g in db_groups:
    if g not in ad_groups:
        cursor.execute("DELETE FROM Groups WHERE Name=%s", (g,))
        deleted_groups += 1

logger.info(f"Удалено групп: {deleted_groups}")

# -------------------------
# CLEAR USERS-GROUPS //Удаляем связи
# -------------------------

cursor.execute("DELETE FROM UsersGroups")
logger.info("UsersGroups очищена")

# -------------------------
# USERS-GROUPS //Связываем
# -------------------------

logger.info("Синхронизация связей пользователей и групп...")

links_count = 0

for g in groups:
    group_name = str(g.cn)

    conn.search(
        search_base=base_dn,
        search_filter=f'(cn={group_name})',
        attributes=['member']
    )

    if not conn.entries:
        continue

    group_entry = conn.entries[0]

    cursor.execute("SELECT Id FROM Groups WHERE Name=%s", (group_name,))
    group_id = cursor.fetchone()

    if not group_id:
        continue

    group_id = group_id[0]

    if hasattr(group_entry, 'member'):

        for member_dn in group_entry.member:

            conn.search(
                search_base=member_dn,
                search_filter='(objectClass=user)',
                attributes=['userPrincipalName']
            )

            if conn.entries:
                upn = str(conn.entries[0].userPrincipalName)

                cursor.execute("SELECT Id FROM Users WHERE UPN=%s", (upn,))
                user_row = cursor.fetchone()

                if user_row:
                    user_id = user_row[0]

                    cursor.execute("""
                        INSERT INTO UsersGroups (UserId, GroupId)
                        VALUES (%s, %s)
                        ON CONFLICT DO NOTHING
                    """, (user_id, group_id))

                    links_count += 1

logger.info(f"Создано связей UsersGroups: {links_count}")

# -------------------------
# COMMIT //Сохраняем и закрываем соединение
# -------------------------

db.commit()

cursor.close()
db.close()
conn.unbind()

logger.info("Скрипт завершил работу")