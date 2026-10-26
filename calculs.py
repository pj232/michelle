"""
calculs.py
----------
Fonctions d'ingénierie (sans dépendance à Streamlit, donc testables) :
  - contrôle de conformité du béton (EN 206 simplifié)
  - avancement, courbe en S, indicateurs de valeur acquise (EVM)
  - pré-dimensionnement béton armé (Eurocode 2 simplifié) :
    poutre, dalle, poteau, semelle isolée

AVERTISSEMENT : ces calculs sont des outils de PRÉ-DIMENSIONNEMENT.
Ils ne remplacent ni une note de calcul complète ni la vérification
par un bureau d'études agréé.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

CLASSES_BETON = {"C20/25": 20.0, "C25/30": 25.0, "C30/37": 30.0, "C35/45": 35.0, "C40/50": 40.0}

# ============================================================== 1. BÉTON (EN 206)


def resistance_28j_estimee(resistance: float, age_jours: int, s: float = 0.25) -> float:
    """Ramène une résistance mesurée à t jours à 28 jours (EC2 §3.1.2 : βcc(t))."""
    if age_jours <= 0:
        return float("nan")
    beta_cc = math.exp(s * (1 - math.sqrt(28 / age_jours)))
    return resistance / beta_cc


def evaluer_conformite(resistances: list[float], fck: float) -> dict:
    """
    Critères de conformité à 28 jours (EN 206, production initiale/continue simplifiée).
      n < 15  : fcm >= fck + 4  et  fci >= fck - 4
      n >= 15 : fcm >= fck + 1,48·σ  et  fci >= fck - 4   (σ plancher = 3 MPa)
    """
    x = np.asarray(resistances, dtype=float)
    n = len(x)
    if n < 3:
        return {"n": n, "conforme": None,
                "commentaire": "Au moins 3 résultats à 28 jours sont nécessaires."}
    fcm, fmin = float(x.mean()), float(x.min())
    sigma = float(x.std(ddof=1))
    if n >= 15:
        crit1 = fcm >= fck + 1.48 * max(sigma, 3.0)
        seuil1 = fck + 1.48 * max(sigma, 3.0)
    else:
        crit1 = fcm >= fck + 4
        seuil1 = fck + 4
    crit2 = fmin >= fck - 4
    return {
        "n": n, "fcm": fcm, "fmin": fmin, "sigma": sigma,
        "seuil_fcm": seuil1, "seuil_fci": fck - 4,
        "critere_moyenne": crit1, "critere_individuel": crit2,
        "conforme": bool(crit1 and crit2),
        "commentaire": "Conforme" if (crit1 and crit2) else "NON CONFORME",
    }


# ============================================== 2. PLANNING, COURBE EN S, EVM


def _durees(df: pd.DataFrame) -> pd.Series:
    d = (pd.to_datetime(df["fin"]) - pd.to_datetime(df["debut"])).dt.days + 1
    return d.clip(lower=1)


def avancement_reel(taches: pd.DataFrame) -> float:
    """Avancement global pondéré par la durée des tâches (0 à 1)."""
    if taches.empty:
        return 0.0
    w = _durees(taches)
    return float((w * taches["avancement"].fillna(0) / 100).sum() / w.sum())


def avancement_prevu(taches: pd.DataFrame, jour: pd.Timestamp) -> float:
    if taches.empty:
        return 0.0
    deb, fin = pd.to_datetime(taches["debut"]), pd.to_datetime(taches["fin"])
    w = _durees(taches)
    frac = ((jour - deb).dt.days + 1) / w
    return float((w * frac.clip(0, 1)).sum() / w.sum())


def courbe_s(taches: pd.DataFrame, pas_jours: int = 7) -> pd.DataFrame:
    """Avancement cumulé prévu (courbe en S) par pas de temps."""
    if taches.empty:
        return pd.DataFrame(columns=["date", "prevu"])
    d0, d1 = pd.to_datetime(taches["debut"]).min(), pd.to_datetime(taches["fin"]).max()
    dates = list(pd.date_range(d0, d1, freq=f"{pas_jours}D"))
    if dates[-1] != d1:
        dates.append(d1)
    return pd.DataFrame({"date": dates,
                         "prevu": [avancement_prevu(taches, d) * 100 for d in dates]})


@dataclass
class EVM:
    bac: float
    pv: float
    ev: float
    ac: float
    cpi: float = field(init=False)
    spi: float = field(init=False)
    cv: float = field(init=False)
    sv: float = field(init=False)
    eac: float = field(init=False)

    def __post_init__(self):
        self.cpi = self.ev / self.ac if self.ac > 0 else float("nan")
        self.spi = self.ev / self.pv if self.pv > 0 else float("nan")
        self.cv = self.ev - self.ac
        self.sv = self.ev - self.pv
        self.eac = self.bac / self.cpi if self.cpi and not math.isnan(self.cpi) and self.cpi > 0 \
            else float("nan")


def calcul_evm(bac: float, av_reel: float, av_prevu: float, cout_reel: float) -> EVM:
    """Méthode de la valeur acquise : PV, EV, AC, CPI, SPI, EAC."""
    return EVM(bac=bac, pv=bac * av_prevu, ev=bac * av_reel, ac=cout_reel)


# ============================================== 3. PRÉ-DIMENSIONNEMENT BA (EC2)


@dataclass
class Materiaux:
    fck: float = 25.0       # MPa
    fyk: float = 500.0      # MPa
    gamma_c: float = 1.5
    gamma_s: float = 1.15
    alpha_cc: float = 0.85  # valeur prudente (EN 1992-1-1 : 0,85 à 1,0 selon AN)

    @property
    def fcd(self) -> float:
        return self.alpha_cc * self.fck / self.gamma_c

    @property
    def fyd(self) -> float:
        return self.fyk / self.gamma_s

    @property
    def fctm(self) -> float:
        return 0.30 * self.fck ** (2 / 3) if self.fck <= 50 else 2.12 * math.log(1 + (self.fck + 8) / 10)


def combinaisons(g: float, q: float) -> tuple[float, float]:
    """(ELU, ELS) : 1,35G + 1,5Q  et  G + Q."""
    return 1.35 * g + 1.5 * q, g + q


XI_LIM = 0.45                                   # x/d limite (fck <= 50 MPa, ductilité)
MU_LIM = 0.8 * XI_LIM * (1 - 0.4 * XI_LIM)      # ≈ 0,295


def armature_flexion(m_knm: float, b_mm: float, d_mm: float, h_mm: float, mat: Materiaux) -> dict:
    """Section rectangulaire en flexion simple, ELU (diagramme rectangulaire)."""
    m = abs(m_knm) * 1e6  # N.mm
    mu = m / (b_mm * d_mm**2 * mat.fcd)
    as_min = max(0.26 * mat.fctm / mat.fyk * b_mm * d_mm, 0.0013 * b_mm * d_mm)
    as_max = 0.04 * b_mm * h_mm
    if mu > MU_LIM:
        return {"mu": mu, "ok": False, "as_calc": float("nan"), "as_min": as_min,
                "as_max": as_max, "as_req": float("nan"),
                "message": "μ > μ_lim : armatures comprimées requises → augmenter la section."}
    alpha = 1.25 * (1 - math.sqrt(1 - 2 * mu))
    z = d_mm * (1 - 0.4 * alpha)
    as_calc = m / (z * mat.fyd)
    as_req = max(as_calc, as_min)
    ok = as_req <= as_max
    return {"mu": mu, "z": z, "ok": ok, "as_calc": as_calc, "as_min": as_min,
            "as_max": as_max, "as_req": as_req,
            "message": "OK" if ok else "As > As,max (4 % Ac) → augmenter la section."}


def proposer_barres(as_req_mm2: float, b_mm: float, enrobage_mm: float,
                    phi_etrier: float = 8, n_min: int = 2) -> str:
    """
    Première combinaison de HA dont l'espacement libre est admissible.
    n_min = 2 pour une poutre (1 lit) ; n_min = 4 pour un poteau (barres réparties
    sur 2 faces : n/2 barres par face).
    """
    if math.isnan(as_req_mm2):
        return "—"
    largeur = b_mm - 2 * (enrobage_mm + phi_etrier)
    par_face = 2 if n_min >= 4 else 1
    for phi in (12, 14, 16, 20, 25, 32):
        a1 = math.pi * phi**2 / 4
        n = max(n_min, math.ceil(as_req_mm2 / a1))
        if par_face == 2 and n % 2:
            n += 1
        n_lit = n // par_face
        espace = max(phi, 20)  # espacement libre minimal ≈ max(φ ; dg+5 ; 20 mm)
        if n_lit * phi + (n_lit - 1) * espace <= largeur:
            return f"{n} HA{phi}  (As = {n * a1 / 100:.2f} cm²)"
    return "Section trop étroite : plusieurs lits ou section plus large."


def proposer_nappe(as_m_mm2: float, h_mm: float) -> str:
    """Nappe de dalle : HA espacés (As par mètre de largeur)."""
    if math.isnan(as_m_mm2):
        return "—"
    s_max = min(3 * h_mm, 400)
    for phi in (8, 10, 12, 14, 16):
        a1 = math.pi * phi**2 / 4
        s = 1000 * a1 / as_m_mm2
        s = math.floor(s / 25) * 25  # arrondi par défaut à 25 mm
        if s >= 100:
            return f"HA{phi} e = {min(s, s_max):.0f} mm"
    return "As très élevé : augmenter l'épaisseur."


RATIO_FLECHE = {"Isostatique": 16, "Continue – travée de rive": 20,
                "Continue – travée intermédiaire": 24}
COEFF_POUTRE = {  # (coef. moment M = k·p·L², coef. effort tranchant V = c·p·L)
    "Isostatique": (1 / 8, 0.50),
    "Continue – travée de rive": (1 / 10, 0.60),
    "Continue – travée intermédiaire": (1 / 12, 0.50),
}


def dimensionner_poutre(portee_m: float, b_mm: float, h_mm: float, g_knm: float, q_knm: float,
                        mat: Materiaux, schema: str = "Isostatique",
                        enrobage_mm: float = 30, phi_etrier: float = 8) -> dict:
    p_elu, p_els = combinaisons(g_knm, q_knm)
    k_m, k_v = COEFF_POUTRE[schema]
    m_ed = k_m * p_elu * portee_m**2
    v_ed = k_v * p_elu * portee_m
    d = h_mm - enrobage_mm - phi_etrier - 10
    flex = armature_flexion(m_ed, b_mm, d, h_mm, mat)

    # --- effort tranchant (EC2 §6.2)
    z = 0.9 * d
    k = min(1 + math.sqrt(200 / d), 2.0)
    rho_l = min((flex["as_req"] if not math.isnan(flex["as_req"]) else 0) / (b_mm * d), 0.02)
    v_min = 0.035 * k**1.5 * math.sqrt(mat.fck)
    v_rdc = max(0.12 * k * (100 * rho_l * mat.fck) ** (1 / 3), v_min) * b_mm * d / 1000  # kN
    nu1 = 0.6 * (1 - mat.fck / 250)
    cot = 2.5
    v_rdmax = b_mm * z * nu1 * mat.fcd / (cot + 1 / cot) / 1000  # kN
    asw_s_min = 0.08 * math.sqrt(mat.fck) / mat.fyk * b_mm          # mm²/mm
    if v_ed > v_rdmax:
        asw_s, msg_v = float("nan"), "V_Ed > V_Rd,max : bielle comprimée insuffisante → augmenter la section."
    elif v_ed <= v_rdc:
        asw_s, msg_v = asw_s_min, "Armatures d'effort tranchant minimales."
    else:
        asw_s = max(v_ed * 1000 / (z * mat.fyd * cot), asw_s_min)
        msg_v = "Armatures d'effort tranchant calculées."

    ratio_ld = portee_m * 1000 / d
    return {
        "p_elu": p_elu, "p_els": p_els, "m_ed": m_ed, "v_ed": v_ed, "d": d,
        "flexion": flex, "barres": proposer_barres(flex["as_req"], b_mm, enrobage_mm, phi_etrier),
        "v_rdc": v_rdc, "v_rdmax": v_rdmax, "asw_s_cm2m": asw_s * 10 if not math.isnan(asw_s) else float("nan"),
        "msg_tranchant": msg_v,
        "ratio_ld": ratio_ld, "ratio_ld_lim": RATIO_FLECHE[schema],
        "fleche_ok": ratio_ld <= RATIO_FLECHE[schema],
    }


def dimensionner_dalle(lx_m: float, ly_m: float, h_mm: float, g_knm2: float, q_knm2: float,
                       mat: Materiaux, continue_: bool = True, enrobage_mm: float = 25) -> dict:
    """Dalle pleine sur 4 appuis (coefficients BAEL, ν = 0) ou dalle portant dans un sens."""
    lx, ly = min(lx_m, ly_m), max(lx_m, ly_m)
    rho = lx / ly
    p_elu, p_els = combinaisons(g_knm2 + 25 * h_mm / 1000, q_knm2)  # + poids propre
    if rho < 0.4:
        mx, my, sens = p_elu * lx**2 / 8, 0.0, "1 sens (porte selon Lx)"
    else:
        mu_x = 1 / (8 * (1 + 2.4 * rho**3))
        mu_y = rho**2 * (1 - 0.95 * (1 - rho) ** 2)
        mx = mu_x * p_elu * lx**2
        my = mu_y * mx
        sens = "2 sens"
    coef_trav, coef_app = (0.75, 0.5) if continue_ else (1.0, 0.0)
    d_x = h_mm - enrobage_mm - 5
    d_y = d_x - 10
    res = {
        "rho": rho, "sens": sens, "p_elu": p_elu, "p_els": p_els,
        "mx": mx * coef_trav, "my": my * coef_trav, "m_appui": max(mx, my) * coef_app,
        "d_x": d_x,
    }
    for nom, m, d in (("x", res["mx"], d_x), ("y", res["my"], d_y),
                      ("appui", res["m_appui"], d_x)):
        if m <= 0:
            res[f"flex_{nom}"], res[f"nappe_{nom}"] = None, "—"
            continue
        f = armature_flexion(m, 1000, d, h_mm, mat)
        res[f"flex_{nom}"] = f
        res[f"nappe_{nom}"] = proposer_nappe(f["as_req"], h_mm)
    res["ratio_ld"] = lx * 1000 / d_x
    res["ratio_ld_lim"] = 30 if continue_ else 25
    res["fleche_ok"] = res["ratio_ld"] <= res["ratio_ld_lim"]
    return res


def dimensionner_poteau(n_ed_kn: float, b_mm: float, h_mm: float, lf_m: float,
                        mat: Materiaux) -> dict:
    """
    Poteau comprimé – méthode simplifiée de type « colonne modèle » (BAEL B.8.4) :
      N_u = α · [ B_r·fc28 / (0,9·γb) + A_s·fe / γs ]
    Valable pour λ ≤ 70, compression centrée.
    """
    lam = lf_m * 1000 * math.sqrt(12) / min(b_mm, h_mm)
    if lam > 70:
        return {"lambda": lam, "valide": False,
                "message": "λ > 70 : hors domaine de la méthode simplifiée – augmenter la section."}
    alpha = 0.85 / (1 + 0.2 * (lam / 35) ** 2) if lam <= 50 else 0.6 * (50 / lam) ** 2
    br = (b_mm - 20) * (h_mm - 20)
    ac = b_mm * h_mm
    n = n_ed_kn * 1000
    as_calc = (n / alpha - br * mat.fck / (0.9 * mat.gamma_c)) * mat.gamma_s / mat.fyk
    as_min = max(0.10 * n / mat.fyd, 0.002 * ac)
    as_max = 0.04 * ac
    as_req = max(as_calc, as_min)
    ok = as_req <= as_max
    n_rd_beton = alpha * br * mat.fck / (0.9 * mat.gamma_c) / 1000
    return {
        "lambda": lam, "alpha": alpha, "valide": True, "as_calc": as_calc,
        "as_min": as_min, "as_max": as_max, "as_req": as_req, "ok": ok,
        "n_rd_beton_seul": n_rd_beton,
        "barres": proposer_barres(as_req, b_mm, 30, n_min=4) if ok else "—",
        "message": "OK" if ok else "As > 4 % Ac : augmenter la section ou la classe de béton.",
    }


def dimensionner_semelle(n_ser_kn: float, n_elu_kn: float, sigma_sol_kpa: float,
                         a_poteau_mm: float, b_poteau_mm: float, mat: Materiaux,
                         majoration_pp: float = 0.10, enrobage_mm: float = 50) -> dict:
    """Semelle isolée rectangulaire homothétique au poteau, méthode des bielles."""
    s_req = n_ser_kn * (1 + majoration_pp) / sigma_sol_kpa  # m²
    ratio = a_poteau_mm / b_poteau_mm
    b_m = math.sqrt(s_req / ratio)
    a_m = b_m * ratio
    a_m, b_m = math.ceil(a_m * 20) / 20, math.ceil(b_m * 20) / 20  # arrondi 5 cm
    a_p, b_p = a_poteau_mm / 1000, b_poteau_mm / 1000
    d_min = max((a_m - a_p) / 4, (b_m - b_p) / 4)
    d = math.ceil(d_min * 20) / 20 if d_min > 0 else 0.20
    d = max(d, 0.20)
    h = d + enrobage_mm / 1000
    sigma_elu = n_elu_kn / (a_m * b_m)
    as_a = n_elu_kn * (a_m - a_p) / (8 * d * mat.fyd * 1000) * 1e6  # mm² (nappe // A)
    as_b = n_elu_kn * (b_m - b_p) / (8 * d * mat.fyd * 1000) * 1e6
    return {"surface_req": s_req, "A": a_m, "B": b_m, "d": d, "h": h,
            "sigma_ser": n_ser_kn * (1 + majoration_pp) / (a_m * b_m),
            "sigma_elu": sigma_elu, "as_a_mm2": as_a, "as_b_mm2": as_b,
            "vol_beton_m3": a_m * b_m * h}


# ============================================================ 4. MÉTRÉS (ratios)


def metres_estimatifs(surface_plancher_m2: float, niveaux: int, ratio_beton_m3_m2: float,
                      ratio_acier_kg_m3: float, ratio_coffrage_m2_m3: float,
                      ratio_maconnerie: float) -> pd.DataFrame:
    shon = surface_plancher_m2 * niveaux
    beton = shon * ratio_beton_m3_m2
    return pd.DataFrame([
        {"Poste": "Béton armé (structure)", "Unité": "m³", "Quantité": beton},
        {"Poste": "Acier HA", "Unité": "kg", "Quantité": beton * ratio_acier_kg_m3},
        {"Poste": "Coffrage", "Unité": "m²", "Quantité": beton * ratio_coffrage_m2_m3},
        {"Poste": "Maçonnerie (cloisons/façades)", "Unité": "m²", "Quantité": shon * ratio_maconnerie},
    ])
