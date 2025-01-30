#!/usr/bin/env python3

import sqlite3

def create_db():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()

    # Drop the old tables if you want a clean start:
    c.execute("DROP TABLE IF EXISTS karteikarten")
    c.execute("DROP TABLE IF EXISTS users")

    # Create the 'users' table
    c.execute("""
    CREATE TABLE users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        verified INTEGER DEFAULT 0,
        verification_code TEXT
    )
    """)

    # Create the 'karteikarten' table
    # This table references users.id as foreign key
    c.execute("""
    CREATE TABLE karteikarten (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        question TEXT NOT NULL,
        solution TEXT NOT NULL,
        formula TEXT,
        lecture TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """)

    conn.commit()
    conn.close()
    print("Database with 'users' and 'karteikarten' tables created successfully.")

if __name__ == "__main__":
    create_db()
