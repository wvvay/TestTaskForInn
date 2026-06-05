from ldap3 import Server, Connection, ALL
import psycopg2

# -------------------------
# CONNECT TO ACTIVE DIRECTORY
# -------------------------

server = Server('192.168.0.5', get_info=ALL)

conn = Connection(
    server,
    user='BOSS\\Администратор',
    password='mudarisov@2003',
    auto_bind=True
)

print("AD подключен")

# -------------------------
# CONNECT TO POSTGRES
# -------------------------

db = psycopg2.connect(
    host="localhost",
    database="BossDB",
    port="5432",
    user="boss",
    password="boss"
)

cursor = db.cursor()

print("PostgreSQL подключен")

# -------------------------
# GET USERS FROM AD
# -------------------------

conn.search(
    search_base='dc=boss,dc=com',
    search_filter='(&(objectClass=user)(userPrincipalName=*))',
    attributes=[
        'userPrincipalName',
        'sAMAccountName',
        'givenName',
        'sn'
    ]
)

users = conn.entries

# -------------------------
# INSERT USERS
# -------------------------

for u in users:
    upn = str(u.userPrincipalName)
    sam = str(u.sAMAccountName)
    first = str(u.givenName)
    last = str(u.sn)

    cursor.execute("""
        INSERT INTO Users (UPN, SAMAccountName, FirstName, LastName)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (UPN) DO UPDATE SET
            SAMAccountName = EXCLUDED.SAMAccountName,
            FirstName = EXCLUDED.FirstName,
            LastName = EXCLUDED.LastName
    """, (upn, sam, first, last))

print("Users синхронизированы")

# -------------------------
# GET GROUPS FROM AD
# -------------------------

conn.search(
    search_base='dc=boss,dc=com',
    search_filter='(objectClass=group)',
    attributes=['cn']
)

groups = conn.entries

# -------------------------
# INSERT GROUPS
# -------------------------

for g in groups:
    name = str(g.cn)

    cursor.execute("""
        INSERT INTO Groups (Name)
        VALUES (%s)
        ON CONFLICT (Name) DO NOTHING
    """, (name,))

print("Groups синхронизированы")

# -------------------------
# BUILD USERS-GROUPS RELATIONSHIP
# -------------------------

for g in groups:
    group_name = str(g.cn)

    # get group DN members
    conn.search(
        search_base='dc=boss,dc=com',
        search_filter=f'(cn={group_name})',
        attributes=['member']
    )

    if not conn.entries:
        continue

    group_entry = conn.entries[0]

    cursor.execute("SELECT Id FROM Groups WHERE Name=%s", (group_name,))
    group_id = cursor.fetchone()[0]

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

print("UsersGroups синхронизированы")

# -------------------------
# COMMIT
# -------------------------

db.commit()

cursor.close()
db.close()
conn.unbind()

print("ГОТОВО: синхронизация завершена")