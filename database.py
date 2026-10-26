"""
database.py
-----------
Couche d'accès aux données (SQLite) de l'application BâtiGest.

Toutes les requêtes sont paramétrées et les noms de tables/colonnes sont
validés par liste blanche afin d'éviter toute injection SQL.
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

import pandas as pd

DB_PATH = Path(__file__).resolve().parent / "data" / "chantier.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS projets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    nom             TEXT NOT NULL,
    maitre_ouvrage  TEXT,
    localisation    TEXT,
    type_batiment   TEXT,
    niveaux         INTEGER DEFAULT 1,
    surface_m2      REAL DEFAULT 0,
    budget_fcfa     REAL DEFAULT 0,
    date_debut      TEXT,
    date_fin        TEXT
);

CREATE TABLE IF NOT EXISTS essais_beton (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    projet_id       INTEGER NOT NULL REFERENCES projets(id) ON DELETE CASCADE,
    date_coulage    TEXT NOT NULL,
    ouvrage         TEXT,
    classe          TEXT NOT NULL,
    fck             REAL NOT NULL,
    age_jours       INTEGER NOT NULL,
    resistance_mpa  REAL NOT NULL,
    affaissement_mm REAL,
    temperature_c   REAL
);

CREATE TABLE IF NOT EXISTS taches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    projet_id       INTEGER NOT NULL REFERENCES projets(id) ON DELETE CASCADE,
    lot             TEXT NOT NULL,
    tache           TEXT NOT NULL,
    debut           TEXT NOT NULL,
    fin             TEXT NOT NULL,
    avancement      REAL DEFAULT 0,
    responsable     TEXT
);

CREATE TABLE IF NOT EXISTS budget (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    projet_id       INTEGER NOT NULL REFERENCES projets(id) ON DELETE CASCADE,
    lot             TEXT NOT NULL,
    montant_prevu   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS depenses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    projet_id       INTEGER NOT NULL REFERENCES projets(id) ON DELETE CASCADE,
    date            TEXT NOT NULL,
    lot             TEXT NOT NULL,
    description     TEXT,
    fournisseur     TEXT,
    montant         REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS non_conformites (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    projet_id       INTEGER NOT NULL REFERENCES projets(id) ON DELETE CASCADE,
    date            TEXT NOT NULL,
    lot             TEXT,
    description     TEXT NOT NULL,
    gravite         TEXT NOT NULL,
    statut          TEXT NOT NULL DEFAULT 'Ouverte',
    action_corrective TEXT
);
"""

TABLES = {
    "projets", "essais_beton", "taches", "budget", "depenses", "non_conformites",
}
_ORDER_RE = re.compile(r"^[a-z_]+( (ASC|DESC))?$")


@contextmanager
def connexion():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connexion() as conn:
        conn.executescript(SCHEMA)


# ----------------------------------------------------------------- utilitaires
def _table(nom: str) -> str:
    if nom not in TABLES:
        raise ValueError(f"Table inconnue : {nom}")
    return nom


def _colonnes(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({_table(table)})")}


def _norm(v):
    if v is None:
        return None
    if isinstance(v, (date, datetime)):  # inclut pd.Timestamp
        return v.isoformat()[:10]
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(v, "item"):  # types numpy -> types python
        return v.item()
    return v


def _filtrer(conn, table: str, data: dict) -> dict:
    cols = _colonnes(conn, table) - {"id"}
    inconnues = set(data) - cols
    if inconnues:
        raise ValueError(f"Colonnes inconnues pour {table} : {inconnues}")
    return {k: _norm(v) for k, v in data.items()}


# ------------------------------------------------------------------------- CRUD
def insert(table: str, data: dict) -> int:
    with connexion() as conn:
        d = _filtrer(conn, table, data)
        cols = ", ".join(d)
        marks = ", ".join("?" for _ in d)
        cur = conn.execute(
            f"INSERT INTO {_table(table)} ({cols}) VALUES ({marks})", list(d.values())
        )
        return cur.lastrowid


def update(table: str, row_id: int, data: dict) -> None:
    with connexion() as conn:
        d = _filtrer(conn, table, data)
        sets = ", ".join(f"{k} = ?" for k in d)
        conn.execute(
            f"UPDATE {_table(table)} SET {sets} WHERE id = ?", [*d.values(), int(row_id)]
        )


def delete(table: str, row_id: int) -> None:
    with connexion() as conn:
        conn.execute(f"DELETE FROM {_table(table)} WHERE id = ?", (int(row_id),))


def fetch(table: str, projet_id: int | None = None, order_by: str = "id") -> pd.DataFrame:
    if not _ORDER_RE.match(order_by):
        raise ValueError("order_by invalide")
    with connexion() as conn:
        if projet_id is None:
            q, params = f"SELECT * FROM {_table(table)} ORDER BY {order_by}", ()
        else:
            q = f"SELECT * FROM {_table(table)} WHERE projet_id = ? ORDER BY {order_by}"
            params = (int(projet_id),)
        return pd.read_sql_query(q, conn, params=params)


def liste_projets() -> pd.DataFrame:
    return fetch("projets", order_by="nom")


def get_projet(projet_id: int) -> dict | None:
    with connexion() as conn:
        row = conn.execute("SELECT * FROM projets WHERE id = ?", (int(projet_id),)).fetchone()
        return dict(row) if row else None
