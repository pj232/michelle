"""
app.py – BâtiGest : collecte, analyse et gestion des données d'un chantier de bâtiment.
Lancer avec :  streamlit run app.py
"""
from __future__ import annotations

import io
import math
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import calculs as calc
import database as db

st.set_page_config(page_title="BâtiGest – Suivi de chantier", page_icon="🏗️", layout="wide")
db.init_db()

LOTS = ["Terrassement", "Fondations", "Gros œuvre", "Maçonnerie", "Charpente / Couverture",
        "Étanchéité", "Menuiseries", "Électricité", "Plomberie / Sanitaire", "Revêtements",
        "Peinture", "VRD / Aménagements", "Installation de chantier", "Autres"]
TYPES_BATIMENT = ["Logement individuel", "Immeuble collectif", "Bureaux", "Établissement scolaire",
                  "Établissement de santé", "Commerce", "Industriel / Entrepôt", "Autre"]
GRAVITES = ["Mineure", "Majeure", "Critique"]
STATUTS = ["Ouverte", "En traitement", "Clôturée"]


# ----------------------------------------------------------------- utilitaires UI
def fcfa(x: float) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    return f"{x:,.0f}".replace(",", " ") + " FCFA"


def to_dates(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    df = df.copy()
    for c in cols:
        df[c] = pd.to_datetime(df[c])
    return df


def bloc_suppression(table: str, df: pd.DataFrame, libelle: callable, cle: str) -> None:
    if df.empty:
        return
    with st.expander("🗑️ Supprimer un enregistrement"):
        options = {int(r["id"]): libelle(r) for _, r in df.iterrows()}
        choix = st.selectbox("Enregistrement", list(options), format_func=options.get,
                             key=f"sel_{cle}")
        if st.button("Supprimer définitivement", key=f"del_{cle}", type="secondary"):
            db.delete(table, choix)
            st.rerun()


def telecharger_excel(feuilles: dict[str, pd.DataFrame]) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xl:
        for nom, df in feuilles.items():
            df.to_excel(xl, sheet_name=nom[:31], index=False)
            ws = xl.sheets[nom[:31]]
            for col in ws.columns:
                largeur = max(len(str(c.value)) if c.value is not None else 0 for c in col)
                ws.column_dimensions[col[0].column_letter].width = min(largeur + 3, 50)
    return buf.getvalue()


# ------------------------------------------------------------------ projet (formulaire)
def formulaire_projet(valeurs: dict | None = None, cle: str = "nouveau") -> dict | None:
    v = valeurs or {}
    with st.form(f"form_projet_{cle}"):
        c1, c2 = st.columns(2)
        nom = c1.text_input("Nom du projet *", v.get("nom", ""))
        mo = c2.text_input("Maître d'ouvrage", v.get("maitre_ouvrage", "") or "")
        loc = c1.text_input("Localisation", v.get("localisation", "") or "")
        type_b = c2.selectbox("Type de bâtiment", TYPES_BATIMENT,
                              index=TYPES_BATIMENT.index(v["type_batiment"])
                              if v.get("type_batiment") in TYPES_BATIMENT else 0)
        niv = c1.number_input("Nombre de niveaux (RDC + étages)", 1, 60, int(v.get("niveaux", 2) or 2))
        surf = c2.number_input("Surface de plancher par niveau (m²)", 0.0, 1e6,
                               float(v.get("surface_m2", 150.0) or 0.0), step=10.0)
        budget = c1.number_input("Budget total (FCFA)", 0.0, 1e13,
                                 float(v.get("budget_fcfa", 0.0) or 0.0), step=1_000_000.0,
                                 format="%.0f")
        d0 = c1.date_input("Date de début", pd.to_datetime(v["date_debut"]).date()
                           if v.get("date_debut") else date.today())
        d1 = c2.date_input("Date de fin contractuelle", pd.to_datetime(v["date_fin"]).date()
                           if v.get("date_fin") else date.today() + timedelta(days=365))
        ok = st.form_submit_button("Enregistrer", type="primary")
    if not ok:
        return None
    if not nom.strip():
        st.error("Le nom du projet est obligatoire.")
        return None
    if d1 < d0:
        st.error("La date de fin doit être postérieure à la date de début.")
        return None
    return {"nom": nom.strip(), "maitre_ouvrage": mo, "localisation": loc,
            "type_batiment": type_b, "niveaux": int(niv), "surface_m2": surf,
            "budget_fcfa": budget, "date_debut": d0, "date_fin": d1}


# ----------------------------------------------------------------------- PAGES
def page_tableau_bord(p: dict) -> None:
    st.header("📊 Tableau de bord")
    taches = db.fetch("taches", p["id"])
    dep = db.fetch("depenses", p["id"])
    bud = db.fetch("budget", p["id"])
    essais = db.fetch("essais_beton", p["id"])
    nc = db.fetch("non_conformites", p["id"])

    bac = float(bud["montant_prevu"].sum()) if not bud.empty else float(p["budget_fcfa"] or 0)
    cout = float(dep["montant"].sum()) if not dep.empty else 0.0
    av_r = calc.avancement_reel(taches)
    av_p = calc.avancement_prevu(taches, pd.Timestamp(date.today()))
    evm = calc.calcul_evm(bac, av_r, av_p, cout)

    c = st.columns(5)
    c[0].metric("Avancement réel", f"{av_r*100:.1f} %", f"{(av_r-av_p)*100:+.1f} pts vs prévu")
    c[1].metric("Avancement prévu", f"{av_p*100:.1f} %")
    c[2].metric("Budget consommé", f"{cout/bac*100:.1f} %" if bac else "—")
    c[3].metric("SPI (délais)", "—" if math.isnan(evm.spi) else f"{evm.spi:.2f}",
                help="SPI = EV / PV. < 1 : retard sur le planning.")
    c[4].metric("CPI (coûts)", "—" if math.isnan(evm.cpi) else f"{evm.cpi:.2f}",
                help="CPI = EV / AC. < 1 : dépassement de budget.")

    c = st.columns(4)
    c[0].metric("Budget (BAC)", fcfa(bac))
    c[1].metric("Dépenses (AC)", fcfa(cout))
    c[2].metric("Estimation à terminaison", fcfa(evm.eac),
                help="EAC = BAC / CPI (projection si la performance actuelle se maintient).")
    n_ouv = int((nc["statut"] != "Clôturée").sum()) if not nc.empty else 0
    c[3].metric("Non-conformités ouvertes", n_ouv)

    if not math.isnan(evm.spi) and evm.spi < 0.9:
        st.warning("⏱️ Retard significatif sur le planning (SPI < 0,90).")
    if not math.isnan(evm.cpi) and evm.cpi < 0.9:
        st.warning("💰 Dérive des coûts importante (CPI < 0,90).")

    g1, g2 = st.columns(2)
    with g1:
        st.subheader("Courbe en S")
        cs = calc.courbe_s(taches)
        if cs.empty:
            st.info("Saisissez des tâches dans « Planning ».")
        else:
            fig = go.Figure()
            fig.add_scatter(x=cs["date"], y=cs["prevu"], mode="lines", name="Prévu")
            fig.add_scatter(x=[pd.Timestamp(date.today())], y=[av_r * 100], mode="markers",
                            name="Réel (aujourd'hui)", marker=dict(size=12, color="red"))
            fig.update_layout(yaxis_title="Avancement cumulé (%)", height=350,
                              margin=dict(t=10, b=10))
            st.plotly_chart(fig, width="stretch")
    with g2:
        st.subheader("Budget prévu vs dépensé par lot")
        if bud.empty and dep.empty:
            st.info("Saisissez le budget et les dépenses dans « Budget & coûts ».")
        else:
            prev = bud.groupby("lot")["montant_prevu"].sum()
            reel = dep.groupby("lot")["montant"].sum() if not dep.empty else pd.Series(dtype=float)
            comp = pd.DataFrame({"Prévu": prev, "Dépensé": reel}).fillna(0).reset_index(names="lot")
            fig = px.bar(comp.melt("lot", var_name="Type", value_name="FCFA"), x="lot", y="FCFA",
                         color="Type", barmode="group")
            fig.update_layout(height=350, margin=dict(t=10, b=10), xaxis_title=None)
            st.plotly_chart(fig, width="stretch")

    st.subheader("Qualité du béton")
    e28 = essais[essais["age_jours"] == 28] if not essais.empty else essais
    if e28.empty:
        st.caption("Aucun essai à 28 jours enregistré.")
    else:
        lignes = []
        for classe, g in e28.groupby("classe"):
            r = calc.evaluer_conformite(g["resistance_mpa"].tolist(), float(g["fck"].iloc[0]))
            lignes.append({"Classe": classe, "n": r["n"],
                           "fcm (MPa)": round(r.get("fcm", float("nan")), 1),
                           "Statut": r["commentaire"]})
        st.dataframe(pd.DataFrame(lignes), hide_index=True, width="stretch")


def page_projet(p: dict) -> None:
    st.header("🏢 Fiche projet")
    c1, c2, c3 = st.columns(3)
    c1.metric("Type", p["type_batiment"] or "—")
    c2.metric("Niveaux", p["niveaux"])
    c3.metric("Surface totale", f"{(p['surface_m2'] or 0) * (p['niveaux'] or 1):,.0f} m²")
    st.write(f"**Maître d'ouvrage :** {p['maitre_ouvrage'] or '—'}  \n"
             f"**Localisation :** {p['localisation'] or '—'}  \n"
             f"**Période :** {p['date_debut']} → {p['date_fin']}")
    with st.expander("✏️ Modifier le projet"):
        nv = formulaire_projet(p, cle="edition")
        if nv:
            db.update("projets", p["id"], nv)
            st.success("Projet mis à jour.")
            st.rerun()
    with st.expander("⚠️ Supprimer le projet"):
        st.error("Supprime aussi toutes les données associées (irréversible).")
        if st.checkbox("Je confirme la suppression", key="conf_del_proj") and \
                st.button("Supprimer le projet"):
            db.delete("projets", p["id"])
            st.session_state.pop("projet_id", None)
            st.rerun()


def page_beton(p: dict) -> None:
    st.header("🧱 Contrôle du béton")
    with st.form("form_beton", clear_on_submit=True):
        c = st.columns(4)
        d = c[0].date_input("Date de coulage", date.today())
        ouvrage = c[1].text_input("Ouvrage", placeholder="ex. Poteaux RDC")
        classe = c[2].selectbox("Classe de résistance", list(calc.CLASSES_BETON), index=1)
        age = c[3].selectbox("Âge à l'écrasement (jours)", [3, 7, 14, 28, 56], index=3)
        c = st.columns(3)
        res = c[0].number_input("Résistance en compression (MPa)", 0.0, 120.0, 25.0, step=0.5)
        slump = c[1].number_input("Affaissement – cône d'Abrams (mm)", 0.0, 300.0, 100.0, step=5.0)
        temp = c[2].number_input("Température du béton (°C)", 0.0, 50.0, 28.0, step=0.5)
        if st.form_submit_button("Ajouter l'essai", type="primary"):
            db.insert("essais_beton", {
                "projet_id": p["id"], "date_coulage": d, "ouvrage": ouvrage, "classe": classe,
                "fck": calc.CLASSES_BETON[classe], "age_jours": age, "resistance_mpa": res,
                "affaissement_mm": slump, "temperature_c": temp})
            st.success("Essai enregistré.")
            st.rerun()

    with st.expander("📥 Importer des essais depuis un fichier CSV"):
        st.caption("Colonnes : date_coulage, ouvrage, classe, age_jours, resistance_mpa, "
                   "affaissement_mm, temperature_c")
        f = st.file_uploader("Fichier CSV", type="csv")
        if f is not None and st.button("Importer"):
            try:
                df = pd.read_csv(f, sep=None, engine="python")
                requis = {"date_coulage", "classe", "age_jours", "resistance_mpa"}
                if not requis <= set(df.columns):
                    st.error(f"Colonnes manquantes : {requis - set(df.columns)}")
                elif not df["classe"].isin(calc.CLASSES_BETON).all():
                    st.error(f"Classes admises : {', '.join(calc.CLASSES_BETON)}")
                else:
                    for _, r in df.iterrows():
                        db.insert("essais_beton", {
                            "projet_id": p["id"], "date_coulage": pd.to_datetime(r["date_coulage"]),
                            "ouvrage": r.get("ouvrage"), "classe": r["classe"],
                            "fck": calc.CLASSES_BETON[r["classe"]], "age_jours": int(r["age_jours"]),
                            "resistance_mpa": float(r["resistance_mpa"]),
                            "affaissement_mm": r.get("affaissement_mm"),
                            "temperature_c": r.get("temperature_c")})
                    st.success(f"{len(df)} essais importés.")
                    st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(f"Import impossible : {exc}")

    essais = db.fetch("essais_beton", p["id"], "date_coulage DESC")
    if essais.empty:
        st.info("Aucun essai enregistré.")
        return
    essais["fc28_equivalente"] = [calc.resistance_28j_estimee(r, a) for r, a in
                                  zip(essais["resistance_mpa"], essais["age_jours"])]
    st.dataframe(essais.drop(columns=["projet_id"]).round(1), hide_index=True, width="stretch")
    bloc_suppression("essais_beton", essais,
                     lambda r: f"#{r['id']} – {r['date_coulage']} – {r['ouvrage']} – "
                               f"{r['resistance_mpa']} MPa à {r['age_jours']} j", "beton")

    st.subheader("Conformité (essais à 28 jours)")
    classe_sel = st.selectbox("Classe à analyser", sorted(essais["classe"].unique()))
    sel = essais[(essais["classe"] == classe_sel)]
    e28 = sel[sel["age_jours"] == 28].sort_values("date_coulage")
    fck = calc.CLASSES_BETON[classe_sel]
    r = calc.evaluer_conformite(e28["resistance_mpa"].tolist(), fck)
    if r["conforme"] is None:
        st.info(r["commentaire"])
    else:
        c = st.columns(5)
        c[0].metric("n", r["n"])
        c[1].metric("fcm", f"{r['fcm']:.1f} MPa", f"seuil {r['seuil_fcm']:.1f}")
        c[2].metric("fci (min)", f"{r['fmin']:.1f} MPa", f"seuil {r['seuil_fci']:.1f}")
        c[3].metric("Écart-type", f"{r['sigma']:.2f} MPa")
        c[4].metric("Verdict", "✅ Conforme" if r["conforme"] else "❌ Non conforme")
    if not sel.empty:
        fig = px.scatter(sel.sort_values("date_coulage"), x="date_coulage", y="fc28_equivalente",
                         color=sel["age_jours"].astype(str), hover_data=["ouvrage", "resistance_mpa"],
                         labels={"fc28_equivalente": "Résistance équivalente à 28 j (MPa)",
                                 "color": "Âge (j)", "date_coulage": "Date de coulage"})
        fig.add_hline(y=fck, line_dash="dash", line_color="red", annotation_text=f"fck = {fck:.0f}")
        fig.add_hline(y=fck + 4, line_dash="dot", line_color="green", annotation_text="fck + 4")
        fig.update_layout(height=380)
        st.plotly_chart(fig, width="stretch")
        st.caption("Les résultats à âge ≠ 28 j sont ramenés à 28 j par βcc(t) (EC2 §3.1.2, s = 0,25) "
                   "à titre indicatif ; seule la résistance mesurée à 28 j sert au verdict.")


def page_planning(p: dict) -> None:
    st.header("📅 Planning & avancement")
    with st.form("form_tache", clear_on_submit=True):
        c = st.columns(3)
        lot = c[0].selectbox("Lot", LOTS)
        tache = c[1].text_input("Tâche *")
        resp = c[2].text_input("Responsable")
        c = st.columns(3)
        d0 = c[0].date_input("Début", date.today())
        d1 = c[1].date_input("Fin", date.today() + timedelta(days=14))
        av = c[2].slider("Avancement (%)", 0, 100, 0)
        if st.form_submit_button("Ajouter la tâche", type="primary"):
            if not tache.strip():
                st.error("Le libellé de la tâche est obligatoire.")
            elif d1 < d0:
                st.error("La fin doit être postérieure au début.")
            else:
                db.insert("taches", {"projet_id": p["id"], "lot": lot, "tache": tache.strip(),
                                     "debut": d0, "fin": d1, "avancement": av,
                                     "responsable": resp})
                st.rerun()

    taches = db.fetch("taches", p["id"], "debut")
    if taches.empty:
        st.info("Aucune tâche.")
        return
    t = to_dates(taches, ["debut", "fin"])
    t["debut"], t["fin"] = t["debut"].dt.date, t["fin"].dt.date

    st.subheader("Mise à jour de l'avancement")
    edite = st.data_editor(
        t.drop(columns=["projet_id"]), hide_index=True, width="stretch", key="editor_taches",
        disabled=["id"],
        column_config={
            "lot": st.column_config.SelectboxColumn("Lot", options=LOTS),
            "avancement": st.column_config.NumberColumn("Avancement (%)", min_value=0, max_value=100, step=5),
            "debut": st.column_config.DateColumn("Début"),
            "fin": st.column_config.DateColumn("Fin"),
        })
    if st.button("💾 Enregistrer les modifications"):
        erreurs = 0
        for _, r in edite.iterrows():
            if pd.isna(r["debut"]) or pd.isna(r["fin"]) or r["fin"] < r["debut"]:
                erreurs += 1
                continue
            db.update("taches", r["id"], {k: r[k] for k in
                      ("lot", "tache", "debut", "fin", "avancement", "responsable")})
        st.warning(f"{erreurs} ligne(s) ignorée(s) (dates invalides).") if erreurs \
            else st.success("Modifications enregistrées.")
        st.rerun()
    bloc_suppression("taches", taches, lambda r: f"#{r['id']} – {r['lot']} – {r['tache']}", "tache")

    st.subheader("Diagramme de Gantt")
    g = to_dates(taches, ["debut", "fin"])
    g["fin_aff"] = g["fin"] + pd.Timedelta(days=1)
    fig = px.timeline(g, x_start="debut", x_end="fin_aff", y="tache", color="lot",
                      hover_data=["avancement", "responsable"])
    fig.update_yaxes(autorange="reversed", title=None)
    fig.add_vline(x=pd.Timestamp(date.today()).timestamp() * 1000, line_dash="dash", line_color="red")
    fig.update_layout(height=max(300, 28 * len(g) + 120))
    st.plotly_chart(fig, width="stretch")


def page_budget(p: dict) -> None:
    st.header("💰 Budget & coûts")
    t1, t2 = st.tabs(["Budget prévisionnel par lot", "Dépenses"])
    with t1:
        with st.form("form_budget", clear_on_submit=True):
            c = st.columns(2)
            lot = c[0].selectbox("Lot", LOTS, key="bud_lot")
            m = c[1].number_input("Montant prévu (FCFA)", 0.0, 1e13, 0.0, step=500_000.0, format="%.0f")
            if st.form_submit_button("Ajouter la ligne", type="primary"):
                db.insert("budget", {"projet_id": p["id"], "lot": lot, "montant_prevu": m})
                st.rerun()
        bud = db.fetch("budget", p["id"])
        if not bud.empty:
            st.dataframe(bud.drop(columns="projet_id"), hide_index=True, width="stretch")
            st.metric("Total prévisionnel", fcfa(bud["montant_prevu"].sum()))
            bloc_suppression("budget", bud, lambda r: f"#{r['id']} – {r['lot']} – {fcfa(r['montant_prevu'])}", "bud")
    with t2:
        with st.form("form_dep", clear_on_submit=True):
            c = st.columns(3)
            d = c[0].date_input("Date", date.today(), key="dep_date")
            lot = c[1].selectbox("Lot", LOTS, key="dep_lot")
            m = c[2].number_input("Montant (FCFA)", 0.0, 1e13, 0.0, step=100_000.0, format="%.0f")
            c = st.columns(2)
            desc = c[0].text_input("Description")
            four = c[1].text_input("Fournisseur / Entreprise")
            if st.form_submit_button("Enregistrer la dépense", type="primary"):
                if m <= 0:
                    st.error("Le montant doit être strictement positif.")
                else:
                    db.insert("depenses", {"projet_id": p["id"], "date": d, "lot": lot,
                                           "description": desc, "fournisseur": four, "montant": m})
                    st.rerun()
        dep = db.fetch("depenses", p["id"], "date DESC")
        if dep.empty:
            st.info("Aucune dépense.")
            return
        st.dataframe(dep.drop(columns="projet_id"), hide_index=True, width="stretch")
        bloc_suppression("depenses", dep, lambda r: f"#{r['id']} – {r['date']} – {r['lot']} – {fcfa(r['montant'])}", "dep")
        d = to_dates(dep, ["date"]).sort_values("date")
        d["cumul"] = d["montant"].cumsum()
        fig = px.line(d, x="date", y="cumul", markers=True, labels={"cumul": "Dépenses cumulées (FCFA)"})
        fig.update_layout(height=320)
        st.plotly_chart(fig, width="stretch")


def page_nc(p: dict) -> None:
    st.header("⚠️ Non-conformités & qualité")
    with st.form("form_nc", clear_on_submit=True):
        c = st.columns(3)
        d = c[0].date_input("Date", date.today())
        lot = c[1].selectbox("Lot", LOTS)
        gr = c[2].selectbox("Gravité", GRAVITES)
        desc = st.text_area("Description de la non-conformité *")
        act = st.text_input("Action corrective prévue")
        if st.form_submit_button("Enregistrer", type="primary"):
            if not desc.strip():
                st.error("La description est obligatoire.")
            else:
                db.insert("non_conformites", {"projet_id": p["id"], "date": d, "lot": lot,
                                              "description": desc.strip(), "gravite": gr,
                                              "statut": "Ouverte", "action_corrective": act})
                st.rerun()
    nc = db.fetch("non_conformites", p["id"], "date DESC")
    if nc.empty:
        st.info("Aucune non-conformité enregistrée.")
        return
    edite = st.data_editor(nc.drop(columns="projet_id"), hide_index=True, width="stretch",
                           disabled=["id", "date", "lot", "description", "gravite"],
                           column_config={"statut": st.column_config.SelectboxColumn("Statut", options=STATUTS)})
    if st.button("💾 Enregistrer les statuts"):
        for _, r in edite.iterrows():
            db.update("non_conformites", r["id"], {"statut": r["statut"],
                                                   "action_corrective": r["action_corrective"]})
        st.success("Mis à jour.")
        st.rerun()
    bloc_suppression("non_conformites", nc, lambda r: f"#{r['id']} – {r['lot']} – {r['description'][:50]}", "nc")
    c1, c2 = st.columns(2)
    c1.plotly_chart(px.pie(nc, names="gravite", title="Par gravité"), width="stretch")
    c2.plotly_chart(px.histogram(nc, x="lot", color="statut", title="Par lot et statut"), width="stretch")


def page_dimensionnement(_: dict) -> None:
    st.header("📐 Pré-dimensionnement béton armé (Eurocode 2 simplifié)")
    st.warning("Outil d'aide au **pré-dimensionnement**. Les résultats doivent être vérifiés par une "
               "note de calcul complète (combinaisons, appuis, fissuration, durabilité, séisme/vent) "
               "et validés par un ingénieur habilité.")
    with st.sidebar.expander("Matériaux (dimensionnement)", expanded=False):
        fck = st.selectbox("Béton", list(calc.CLASSES_BETON), index=1, key="dim_fck")
        fyk = st.selectbox("Acier fyk (MPa)", [400, 500], index=1)
        acc = st.selectbox("α_cc", [0.85, 1.0], index=0,
                           help="0,85 : valeur prudente ; 1,0 : valeur recommandée EN 1992-1-1 selon l'Annexe Nationale.")
        mat = calc.Materiaux(fck=calc.CLASSES_BETON[fck], fyk=float(fyk), alpha_cc=acc)
        st.caption(f"fcd = {mat.fcd:.1f} MPa · fyd = {mat.fyd:.0f} MPa · fctm = {mat.fctm:.2f} MPa")

    tp, td, tpo, ts = st.tabs(["Poutre", "Dalle", "Poteau", "Semelle isolée"])

    with tp:
        c = st.columns(3)
        L = c[0].number_input("Portée L (m)", 1.0, 15.0, 5.0, 0.1, key="p_L")
        b = c[1].number_input("Largeur b (mm)", 150, 600, 250, 10, key="p_b")
        h = c[2].number_input("Hauteur h (mm)", 250, 1200, 500, 10, key="p_h")
        c = st.columns(3)
        g = c[0].number_input("G linéique (kN/m) – hors poids propre", 0.0, 200.0, 15.0, 0.5, key="p_g")
        q = c[1].number_input("Q linéique (kN/m)", 0.0, 200.0, 8.0, 0.5, key="p_q")
        sch = c[2].selectbox("Schéma statique", list(calc.COEFF_POUTRE), key="p_sch")
        pp = 25 * b / 1000 * h / 1000
        st.caption(f"Poids propre ajouté : {pp:.2f} kN/m")
        r = calc.dimensionner_poutre(L, b, h, g + pp, q, mat, sch)
        f = r["flexion"]
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("p_ELU", f"{r['p_elu']:.1f} kN/m")
        m2.metric("M_Ed", f"{r['m_ed']:.1f} kN·m")
        m3.metric("V_Ed", f"{r['v_ed']:.1f} kN")
        m4.metric("μ / μ_lim", f"{f['mu']:.3f} / {calc.MU_LIM:.3f}")
        st.markdown("**Flexion**")
        if f["ok"]:
            st.success(f"As,calc = {f['as_calc']/100:.2f} cm² · As,min = {f['as_min']/100:.2f} cm² · "
                       f"As retenue = **{f['as_req']/100:.2f} cm²** → **{r['barres']}**")
        else:
            st.error(f["message"])
        st.markdown("**Effort tranchant**")
        (st.error if "insuffisante" in r["msg_tranchant"] else st.info)(
            f"V_Rd,c = {r['v_rdc']:.1f} kN · V_Rd,max = {r['v_rdmax']:.1f} kN · {r['msg_tranchant']}"
            + ("" if math.isnan(r["asw_s_cm2m"]) else f" · Asw/s = {r['asw_s_cm2m']:.2f} cm²/m"))
        st.markdown("**Déformation (indicatif)**")
        (st.success if r["fleche_ok"] else st.warning)(
            f"L/d = {r['ratio_ld']:.1f} (limite indicative {r['ratio_ld_lim']}) – "
            + ("OK" if r["fleche_ok"] else "vérifier la flèche (EC2 §7.4) ou augmenter h."))

    with td:
        c = st.columns(3)
        lx = c[0].number_input("Lx – petite portée (m)", 1.0, 10.0, 4.0, 0.1, key="d_lx")
        ly = c[1].number_input("Ly – grande portée (m)", 1.0, 15.0, 5.0, 0.1, key="d_ly")
        h = c[2].number_input("Épaisseur h (mm)", 100, 400, 160, 10, key="d_h")
        c = st.columns(3)
        g = c[0].number_input("G (kN/m²) – revêtements, cloisons (hors poids propre)", 0.0, 20.0, 2.0, 0.1, key="d_g")
        q = c[1].number_input("Q (kN/m²)", 0.0, 20.0, 2.5, 0.1, key="d_q")
        cont = c[2].checkbox("Dalle continue (encastrement partiel)", True, key="d_cont")
        r = calc.dimensionner_dalle(lx, ly, h, g, q, mat, cont)
        st.write(f"ρ = Lx/Ly = **{r['rho']:.2f}** → dalle portant **{r['sens']}** · "
                 f"p_ELU = {r['p_elu']:.2f} kN/m²")
        t = pd.DataFrame([
            {"Position": "Travée // Lx", "M (kN·m/m)": r["mx"],
             "As (cm²/m)": r["flex_x"]["as_req"] / 100 if r["flex_x"] else None, "Nappe": r["nappe_x"]},
            {"Position": "Travée // Ly", "M (kN·m/m)": r["my"],
             "As (cm²/m)": r["flex_y"]["as_req"] / 100 if r["flex_y"] else None, "Nappe": r["nappe_y"]},
            {"Position": "Appui continu", "M (kN·m/m)": r["m_appui"],
             "As (cm²/m)": r["flex_appui"]["as_req"] / 100 if r["flex_appui"] else None,
             "Nappe": r["nappe_appui"]}])
        st.dataframe(t.round(2), hide_index=True, width="stretch")
        (st.success if r["fleche_ok"] else st.warning)(
            f"Lx/d = {r['ratio_ld']:.1f} (limite indicative {r['ratio_ld_lim']})")

    with tpo:
        c = st.columns(4)
        n = c[0].number_input("N_Ed ELU (kN)", 10.0, 20000.0, 800.0, 10.0, key="po_n")
        b = c[1].number_input("b (mm)", 200, 1000, 300, 10, key="po_b")
        h = c[2].number_input("h (mm)", 200, 1000, 300, 10, key="po_h")
        lf = c[3].number_input("Longueur de flambement l_f (m)", 0.5, 12.0, 2.5, 0.1, key="po_lf")
        r = calc.dimensionner_poteau(n, b, h, lf, mat)
        if not r["valide"]:
            st.error(f"λ = {r['lambda']:.1f} – {r['message']}")
        else:
            m1, m2, m3 = st.columns(3)
            m1.metric("Élancement λ", f"{r['lambda']:.1f}")
            m2.metric("Coefficient α", f"{r['alpha']:.3f}")
            m3.metric("N_Rd (béton seul)", f"{r['n_rd_beton_seul']:.0f} kN")
            if r["ok"]:
                st.success(f"As,calc = {max(r['as_calc'],0)/100:.2f} cm² · As,min = {r['as_min']/100:.2f} cm² · "
                           f"As retenue = **{r['as_req']/100:.2f} cm²** → **{r['barres']}**")
            else:
                st.error(r["message"])
            st.caption("Compression centrée uniquement : les moments de flexion (excentricités, "
                       "contreventement, 2ᵉ ordre) sont à traiter dans la note de calcul.")

    with ts:
        c = st.columns(3)
        ns = c[0].number_input("N_ser (kN)", 10.0, 20000.0, 600.0, 10.0, key="s_ns")
        nu = c[1].number_input("N_ELU (kN)", 10.0, 30000.0, 820.0, 10.0, key="s_nu")
        sig = c[2].number_input("Contrainte admissible du sol σ_sol (kPa)", 50.0, 1000.0, 200.0, 10.0,
                                help="Issue de l'étude géotechnique (ELS).", key="s_sig")
        c = st.columns(2)
        a = c[0].number_input("Poteau a (mm)", 200, 1000, 300, 10, key="s_a")
        bp = c[1].number_input("Poteau b (mm)", 200, 1000, 300, 10, key="s_b")
        r = calc.dimensionner_semelle(ns, nu, sig, a, bp, mat)
        m = st.columns(4)
        m[0].metric("A × B", f"{r['A']:.2f} × {r['B']:.2f} m")
        m[1].metric("Hauteur utile d", f"{r['d']:.2f} m")
        m[2].metric("Hauteur h", f"{r['h']:.2f} m")
        m[3].metric("σ_ser réel", f"{r['sigma_ser']:.0f} kPa")
        st.write(f"Nappe // A : As = **{r['as_a_mm2']/100:.2f} cm²** · Nappe // B : As = "
                 f"**{r['as_b_mm2']/100:.2f} cm²** · Volume de béton : {r['vol_beton_m3']:.2f} m³")
        if r["sigma_ser"] > sig:
            st.error("σ_ser > σ_sol : revoir les dimensions.")
        st.caption("Majoration de 10 % pour le poids propre de la semelle et des terres. "
                   "Vérifications complémentaires : poinçonnement, tassements, excentricités.")


def page_metres(p: dict) -> None:
    st.header("📏 Métrés estimatifs (ratios)")
    st.caption("Estimation d'avant-projet à partir de ratios usuels : à remplacer par un métré "
               "détaillé issu des plans d'exécution.")
    c = st.columns(4)
    s = c[0].number_input("Surface par niveau (m²)", 0.0, 1e6, float(p["surface_m2"] or 0.0), 10.0)
    n = c[1].number_input("Niveaux", 1, 60, int(p["niveaux"] or 1))
    rb = c[2].number_input("Béton (m³/m² plancher)", 0.10, 0.60, 0.25, 0.01)
    ra = c[3].number_input("Acier (kg/m³ de béton)", 50.0, 200.0, 100.0, 5.0)
    c = st.columns(2)
    rc = c[0].number_input("Coffrage (m²/m³ de béton)", 2.0, 15.0, 8.0, 0.5)
    rm = c[1].number_input("Maçonnerie (m²/m² plancher)", 0.2, 3.0, 1.2, 0.1)
    df = calc.metres_estimatifs(s, n, rb, ra, rc, rm)
    st.subheader("Prix unitaires (FCFA) – valeurs à adapter au marché local")
    pu = st.data_editor(pd.DataFrame({"Poste": df["Poste"], "PU (FCFA)": [0.0] * len(df)}),
                        hide_index=True, disabled=["Poste"], key="pu_edit", width="stretch")
    df["PU (FCFA)"] = pu["PU (FCFA)"].values
    df["Montant (FCFA)"] = df["Quantité"] * df["PU (FCFA)"]
    st.dataframe(df.round(1), hide_index=True, width="stretch")
    st.metric("Total estimé", fcfa(df["Montant (FCFA)"].sum()))


def page_export(p: dict) -> None:
    st.header("📤 Export des données")
    feuilles = {
        "Planning": db.fetch("taches", p["id"]).drop(columns="projet_id"),
        "Essais béton": db.fetch("essais_beton", p["id"]).drop(columns="projet_id"),
        "Budget": db.fetch("budget", p["id"]).drop(columns="projet_id"),
        "Dépenses": db.fetch("depenses", p["id"]).drop(columns="projet_id"),
        "Non-conformités": db.fetch("non_conformites", p["id"]).drop(columns="projet_id"),
    }
    for nom, df in feuilles.items():
        st.write(f"**{nom}** : {len(df)} ligne(s)")
    st.download_button("⬇️ Télécharger le classeur Excel", telecharger_excel(feuilles),
                       file_name=f"{p['nom'].replace(' ', '_')}_{date.today():%Y%m%d}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       type="primary")


# ---------------------------------------------------------------------- MAIN
PAGES = {
    "📊 Tableau de bord": page_tableau_bord,
    "🏢 Fiche projet": page_projet,
    "📅 Planning": page_planning,
    "🧱 Contrôle béton": page_beton,
    "💰 Budget & coûts": page_budget,
    "⚠️ Non-conformités": page_nc,
    "📏 Métrés": page_metres,
    "📐 Pré-dimensionnement": page_dimensionnement,
    "📤 Export": page_export,
}


def main() -> None:
    st.sidebar.title("🏗️ BâtiGest")
    projets = db.liste_projets()

    if projets.empty:
        st.title("Bienvenue dans BâtiGest")
        st.write("Créez votre premier projet pour commencer.")
        nv = formulaire_projet()
        if nv:
            st.session_state["projet_id"] = db.insert("projets", nv)
            st.rerun()
        return

    ids = projets["id"].tolist()
    noms = dict(zip(projets["id"], projets["nom"]))
    defaut = st.session_state.get("projet_id", ids[0])
    idx = ids.index(defaut) if defaut in ids else 0
    pid = st.sidebar.selectbox("Projet", ids, index=idx, format_func=noms.get)
    st.session_state["projet_id"] = pid

    with st.sidebar.expander("➕ Nouveau projet"):
        nv = formulaire_projet()
        if nv:
            st.session_state["projet_id"] = db.insert("projets", nv)
            st.rerun()

    page = st.sidebar.radio("Navigation", list(PAGES), label_visibility="collapsed")
    projet = db.get_projet(pid)
    st.title(projet["nom"])
    PAGES[page](projet)


main()
