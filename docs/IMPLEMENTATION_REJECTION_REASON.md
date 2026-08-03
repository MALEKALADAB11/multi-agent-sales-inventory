# Implémentation: Zone de Texte pour Causes de Rejet

## Contexte et Objectif

### Objectif
Dans le rapport de chaque recommandation inventory, ajouter une zone de texte qui s'ouvre quand l'utilisateur clique sur "rejeter". L'utilisateur peut expliquer la cause du rejet, qui sera sauvegardée et utilisée dans la page supervision pour la section "top causes de rejets".

### Pourquoi c'est TRÈS facile
**Tout existe déjà côté backend:**
- ✅ Table `public.agent_feedback` avec champ `reason`
- ✅ Service `feedback_service.py` avec `record_feedback(reason=...)`
- ✅ Endpoint `/api/v1/feedback` acceptant `reason`
- ✅ Payload `FeedbackPayload` avec `reason: Optional[str]`

**Il n'y a RIEN à faire côté backend - tout est déjà prêt!**

---

## Implémentation (Frontend uniquement)

### 1. Modification du composant de rapport inventory

**Fichier frontend à modifier:** (dépend de votre framework React/Vue/etc.)

**Changements:**
- Ajouter un état local pour contrôler l'affichage de la zone de texte
- Ajouter un champ texte qui s'ouvre quand on clique sur "rejeter"
- Appeler l'endpoint `/api/v1/feedback` avec la raison

### 2. Appel API existant

**Endpoint déjà disponible:**
```
POST /api/v1/feedback
```

**Payload déjà supporté:**
```json
{
  "store_id": "I63",
  "source": "reco",           // Nouvelle valeur à accepter
  "decision": "rejected",
  "ref_id": "recommendation_id",
  "sku": 5030131,
  "action_type": "EXPEDITE",
  "reason": "Stock suffisant, commande non nécessaire",
  "payload": {}
}
```

### 3. Modification nécessaire côté backend (MINIME)

**Fichier:** `app/api/feedback.py`

**Changement:** Ajouter "reco" comme valeur valide pour `source`

```python
class FeedbackPayload(BaseModel):
    store_id: str
    source: str = Field(pattern="^(incitation|hitl|po|reco)$")  # Ajouter "reco"
    decision: str = Field(pattern="^(followed|ignored|approved|rejected)$")
    ref_id: Optional[str] = None
    sku: Optional[int] = None
    action_type: Optional[str] = None
    reason: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
```

**C'est la SEULE modification backend nécessaire!**

---

## Flux Utilisateur

### Avant (actuel)
1. Utilisateur ouvre le rapport d'une recommandation
2. Utilisateur clique sur "rejeter"
3. La recommandation est marquée comme rejetée
4. Aucune raison n'est enregistrée

### Après (implémentation)
1. Utilisateur ouvre le rapport d'une recommandation
2. Utilisateur clique sur "rejeter"
3. **Une zone de texte s'ouvre: "Pourquoi rejetez-vous cette recommandation?"**
4. Utilisateur tape la raison (ex: "Stock suffisant")
5. Utilisateur valide
6. **Appel POST /api/v1/feedback avec reason="..."**
7. La raison est sauvegardée dans `public.agent_feedback.reason`
8. La recommandation est marquée comme rejetée

---

## Utilisation dans la page supervision

### Récupérer les top causes de rejet

**Endpoint déjà disponible:**
```
GET /api/v1/feedback/stats?store_id=I63&days=30
```

**Réponse (déjà implémentée dans `get_feedback_stats`):**
```json
{
  "window_days": 30,
  "recent_rejections": [
    {"reason": "Stock suffisant", "count": 15},
    {"reason": "Délai trop long", "count": 8},
    {"reason": "Budget limité", "count": 5}
  ]
}
```

**Note:** Le champ `recent_rejections` est déjà calculé dans `feedback_service.py` (ligne 90-98). Il suffit de l'utiliser dans le frontend supervision.

---

## Fichiers Impliqués

### Backend (1 fichier à modifier)
1. **`app/api/feedback.py`**
   - Ajouter "reco" dans le pattern de `source` (ligne 28)

### Frontend (1 composant à modifier)
1. **Composant rapport inventory** (emplacement dépend de votre framework)
   - Ajouter état pour la zone de texte
   - Ajouter champ texte
   - Appeler `/api/v1/feedback` avec `reason`

### Base de données (AUCUNE migration)
- Table `public.agent_feedback` existe déjà
- Champ `reason` existe déjà
- **Rien à faire côté DB**

---

## Résumé de l'effort

| Tâche | Effort | Responsabilité |
|-------|--------|----------------|
| Modifier pattern source dans feedback.py | 5 min | Backend |
| Ajouter zone de texte dans frontend | 20 min | Frontend |
| Appeler API avec reason | 10 min | Frontend |
| **Total** | **35 min** | **Frontend + 1 ligne backend** |

---

## Avantages

- **Très rapide à implémenter** (35 min)
- **Aucune migration DB** (table existe déjà)
- **Aucun nouveau service** (service existe déjà)
- **Aucun nouvel endpoint** (endpoint existe déjà)
- **Données immédiatement utilisables** dans supervision
- **Extensible** (peut ajouter d'autres types de feedback plus tard)
