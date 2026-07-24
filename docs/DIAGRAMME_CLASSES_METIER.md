# Diagramme de Classes Métier — Moteur Agentique Retail Ooredoo

> **Portée** : modèle du **domaine métier** (Réseau, Ventes, Stock, Approvisionnement,
> Prévision, Pilotage agentique). Les classes techniques (agents LangGraph, états
> `RetailState`, repositories, orchestrateurs) sont documentées séparément dans
> [DIAGRAMMES_CLASSES.md](DIAGRAMMES_CLASSES.md).
>
> **Source** : dérivé du schéma PostgreSQL réel (`docs/DATABASE_SCHEMA.md`) et des
> migrations Alembic `0001` → `0012`, seule source de vérité du modèle.
>
> **Format** : Mermaid — rendu natif GitHub/GitLab/VS Code, export PNG/SVG via
> <https://mermaid.live> pour insertion dans le rapport.

**Conventions**

| Stéréotype | Signification |
|---|---|
| `<<Entité>>` | Entité métier persistante avec identité propre |
| `<<Référentiel>>` | Donnée de référence peu volatile (catalogue, paramétrage) |
| `<<Événement>>` | Fait daté produit par le système ou un acteur |
| `<<enumeration>>` | Valeurs contraintes (CHECK SQL) |

---

## 1. Vue globale du domaine métier

Vue d'ensemble : les entités pivots et leurs relations. Attributs réduits aux
identifiants et aux propriétés discriminantes pour rester lisible.

```mermaid
classDiagram
    direction TB

    %% ---------- Réseau & acteurs ----------
    class Boutique {
        <<Entité>>
        +storeId : str
        +nom : str
        +ville : str
        +region : str
        +canal : CanalVente
        +actif : bool
    }

    class Conseiller {
        <<Entité>>
        +agentId : int
        +nom : str
        +specialisation : str
        +quotaMensuelCA : Decimal
        +coachScore : Decimal
    }

    class Utilisateur {
        <<Entité>>
        +userId : str
        +role : RoleUtilisateur
        +storeId : str
        +advisorId : str
    }

    %% ---------- Catalogue & ventes ----------
    class Produit {
        <<Référentiel>>
        +sku : int
        +nom : str
        +categorie : str
        +prixTTC : Decimal
        +cycleDeVie : CycleDeVie
        +stockable : bool
    }

    class LigneDeVente {
        <<Événement>>
        +id : long
        +dateVente : datetime
        +quantite : int
        +montantTTC : Decimal
        +marge : Decimal
    }

    class Objectif {
        <<Entité>>
        +dateObjectif : date
        +objectifCA : Decimal
        +objectifTransactions : int
    }

    %% ---------- Stock & approvisionnement ----------
    class NiveauDeStock {
        <<Entité>>
        +quantite : int
        +quantiteReservee : int
        +quantiteDisponible : int
        +joursDeStockRestants : float
    }

    class MouvementDeStock {
        <<Événement>>
        +mouvementId : UUID
        +typeMouvement : TypeMouvement
        +quantite : int
    }

    class ParametreReappro {
        <<Référentiel>>
        +pointCommande : int
        +stockSecurite : int
        +eoq : int
    }

    class BonDeCommande {
        <<Entité>>
        +poId : UUID
        +quantiteCommandee : int
        +statut : StatutBC
        +source : SourceBC
    }

    class Fournisseur {
        <<Référentiel>>
        +supplierId : str
        +nom : str
        +delaiLivraisonMoy : int
        +tauxFiabilite : Decimal
    }

    class DemandeReappro {
        <<Entité>>
        +requestId : str
        +quantite : int
        +urgence : UrgenceDemande
        +statut : StatutDemande
    }

    %% ---------- Prévision & contexte ----------
    class PrevisionDemande {
        <<Entité>>
        +datePrevision : date
        +demande24h : Decimal
        +demandeCorrigee : Decimal
    }

    class AjustementContexte {
        <<Entité>>
        +upliftDemandePct : Decimal
        +signalDominant : str
        +confiance : Decimal
    }

    class Evenement {
        <<Référentiel>>
        +eventId : UUID
        +nom : str
        +dateDebut : date
        +intensite : str
    }

    class Promotion {
        <<Référentiel>>
        +promoId : str
        +remisePct : Decimal
    }

    %% ---------- Pilotage agentique ----------
    class CycleAgentique {
        <<Événement>>
        +cycleId : str
        +declencheur : str
        +niveauUrgence : NiveauUrgence
        +ecartPct : float
    }

    class Alerte {
        <<Événement>>
        +id : int
        +typeAlerte : str
        +severite : str
        +statut : StatutAlerte
    }

    class Recommandation {
        <<Entité>>
        +id : UUID
        +action : str
        +quantiteCommande : int
        +confiance : Decimal
        +statut : StatutRecommandation
    }

    class RevueHumaine {
        <<Entité>>
        +id : UUID
        +statut : StatutRevue
        +approbateur : str
    }

    class FeedbackAgent {
        <<Événement>>
        +source : str
        +decision : DecisionFeedback
    }

    %% ---------- Relations ----------
    Boutique "1" o-- "0..*" Conseiller : emploie
    Boutique "1" --> "0..*" Objectif : fixe
    Utilisateur "0..1" ..> "0..1" Conseiller : incarne
    Utilisateur "*" --> "1" Boutique : rattaché à

    LigneDeVente "*" --> "1" Produit : porte sur
    LigneDeVente "*" --> "1" Conseiller : réalisée par
    LigneDeVente "*" --> "1" Boutique : enregistrée en

    NiveauDeStock "*" --> "1" Produit : mesure
    NiveauDeStock "*" --> "1" Boutique : localisé en
    MouvementDeStock "*" --> "1" Produit : impacte
    MouvementDeStock "*" --> "1" Boutique : dans
    ParametreReappro "1" --> "1" Produit : paramètre
    ParametreReappro "1" --> "1" Boutique : pour

    Fournisseur "1" --> "0..*" BonDeCommande : honore
    BonDeCommande "*" --> "1" Produit : commande
    BonDeCommande "*" --> "1" Boutique : livrée à
    BonDeCommande "0..1" ..> "1" Recommandation : matérialise
    DemandeReappro "0..1" ..> "0..1" BonDeCommande : donne lieu à
    DemandeReappro "*" --> "1" Utilisateur : émise par

    PrevisionDemande "*" --> "1" Produit : prévoit
    PrevisionDemande "*" --> "1" Boutique : sur
    AjustementContexte "*" ..> "1" PrevisionDemande : corrige
    Evenement "0..*" ..> "0..*" AjustementContexte : alimente
    Promotion "0..*" ..> "0..*" AjustementContexte : alimente

    CycleAgentique "1" --> "0..*" Alerte : produit
    Alerte "1" --> "0..*" Recommandation : déclenche
    Recommandation "*" --> "1" Produit : porte sur
    Recommandation "*" --> "1" Boutique : cible
    Recommandation "0..1" --> "0..1" RevueHumaine : escalade vers
    RevueHumaine "1" --> "0..*" FeedbackAgent : génère
    LigneDeVente ..> MouvementDeStock : génère (VENTE)
    BonDeCommande ..> MouvementDeStock : génère (RECEPTION_BC)
```

---

## 2. Sous-domaine « Réseau & Force de vente »

```mermaid
classDiagram
    direction LR

    class Boutique {
        <<Entité>>
        +storeId : str
        +nom : str
        +adresse : str
        +ville : str
        +wilaya : str
        +region : str
        +zoneCommerciale : str
        +canal : CanalVente
        +typeBoutique : str
        +latitude : Decimal
        +longitude : Decimal
        +capaciteConseillers : int
        +managerNom : str
        +estOfficielle : bool
        +rangCARegion : int
        +dateOuverture : date
        +actif : bool
        +caDuJour() Decimal
        +tauxAtteinte(date) float
        +effectifPresent() int
    }

    class Conseiller {
        <<Entité>>
        +agentId : int
        +nom : str
        +role : str
        +email : str
        +telephone : str
        +specialisation : str
        +niveauCertification : int
        +ancienneteMois : int
        +niveauPerformance : str
        +quotaMensuelCA : Decimal
        +quotaActivations : int
        +quotaPostpaye : int
        +coachScore : Decimal
        +dateEmbauche : date
        +dateDepart : date
        +caRealise(date) Decimal
        +ecartVsObjectif(date) float
        +estEnRetard(date) bool
    }

    class Utilisateur {
        <<Entité>>
        +userId : str
        +username : str
        +nomComplet : str
        +role : RoleUtilisateur
        +storeId : str
        +advisorId : str
        +actif : bool
        +derniereConnexion : datetime
        +peutVoirBoutique(storeId) bool
        +peutApprouver() bool
    }

    class Objectif {
        <<Entité>>
        +id : int
        +dateObjectif : date
        +objectifCA : Decimal
        +objectifTransactions : int
        +objectifPanierMoyen : Decimal
    }

    class CibleMensuelle {
        <<Entité>>
        +mois : int
        +annee : int
        +niveau : NiveauCible
        +caCibleMensuel : Decimal
        +caCibleS1..S4 : Decimal
        +activationsTotales : int
        +activationsPostpaye : int
        +ventesTerminaux : int
        +npsCible : Decimal
        +facteurSaisonnier : Decimal
        +cibleJournaliere(date) Decimal
    }

    class KpiConseillerJour {
        <<Entité>>
        +kpiDate : date
        +caRealise : Decimal
        +caCible : Decimal
        +ecartCAPct : Decimal
        +nbTransactions : int
        +panierMoyen : Decimal
        +nbForfaits : int
        +nbTerminaux : int
        +nbPostpaye : int
        +tauxUpsellAccessoire : Decimal
        +rangBoutique : int
        +rangRegion : int
        +npsScore : Decimal
        +niveauUrgence : NiveauUrgence
    }

    class ScriptDeVente {
        <<Référentiel>>
        +id : int
        +categorie : str
        +situation : str
        +action : str
        +produitCible : str
        +argumentVente : str
        +impactObserve : str
        +heureMin : int
        +heureMax : int
        +jourSemaine : int
        +estApplicable(heure, jour) bool
    }

    class SatisfactionClient {
        <<Événement>>
        +feedbackDate : date
        +typeEnquete : str
        +score : Decimal
        +verbatim : str
        +categorieMotif : str
        +resolu : bool
    }

    Boutique "1" o-- "0..*" Conseiller
    Boutique "1" --> "0..*" Objectif
    Conseiller "1" --> "0..*" Objectif
    Conseiller "1" --> "0..*" CibleMensuelle
    Boutique "1" --> "0..*" CibleMensuelle
    Conseiller "1" --> "0..*" KpiConseillerJour : évalué par
    Conseiller "1" --> "0..*" SatisfactionClient : reçoit
    Utilisateur "0..1" ..> "0..1" Conseiller : incarne
    Boutique "1" --> "0..*" ScriptDeVente : contextualise
```

**Règles de gestion**

- `Utilisateur.role` pilote le RBAC : un `vendeur` ne voit que sa boutique et son
  propre périmètre ; un `manager` arbitre les `DemandeReappro` et les `RevueHumaine`.
- `KpiConseillerJour` est unique par `(agentId, kpiDate)` — recalculé quotidiennement.
- `CibleMensuelle.niveau` permet de fixer un objectif AGENT, BOUTIQUE ou REGION.

---

## 3. Sous-domaine « Catalogue & Ventes »

```mermaid
classDiagram
    direction LR

    class Produit {
        <<Référentiel>>
        +sku : int
        +nom : str
        +categorie : str
        +famille : str
        +marque : str
        +modele : str
        +gammeLibelle : str
        +prixHT : Decimal
        +prixTTC : Decimal
        +prixAchatHT : Decimal
        +margePct : Decimal
        +flag4G : bool
        +flag5G : bool
        +flagTerminal : bool
        +flagForfait : bool
        +flagSIM : bool
        +flagRecharge : bool
        +serialisable : bool
        +stockable : bool
        +leadTimeDays : int
        +leadTimeStd : int
        +moq : int
        +holdingCostPct : Decimal
        +orderCost : Decimal
        +cycleDeVie : CycleDeVie
        +dateLancement : date
        +dateEOL : date
        +stockageGB : int
        +ramGB : int
        +couleur : str
        +actif : bool
        +margeUnitaire() Decimal
        +estEnFinDeVie(date) bool
        +necessiteNumeroSerie() bool
    }

    class LigneDeVente {
        <<Événement>>
        +id : long
        +dateTransaction : datetime
        +dateOnly : date
        +heure : int
        +quantite : int
        +prixUnitaire : Decimal
        +montantHT : Decimal
        +montantTTC : Decimal
        +marge : Decimal
        +moyenPaiement : str
    }

    class VenteTempsReel {
        <<Événement>>
        +saleId : UUID
        +dateVente : datetime
        +heure : int
        +designationProduit : str
        +quantite : int
        +montantHT : Decimal
        +montantTVA : Decimal
        +montantTTC : Decimal
    }

    class HistoriqueVentes {
        <<Entité>>
        +recordDate : date
        +quantiteVendue : int
        +chiffreAffaires : Decimal
        +prixUnitaire : Decimal
        +estPromo : bool
        +nomEvenement : str
        +saison : str
        +jourSemaine : int
        +estWeekend : bool
        +estJourEvenement : bool
        +facteurUplift : Decimal
    }

    class InteractionCoach {
        <<Événement>>
        +id : int
        +message : str
        +reponse : str
        +ecartPct : float
        +urgence : NiveauUrgence
        +ragUtilise : bool
        +nbScriptsRAG : int
        +typeConseil : str
        +confiance : float
    }

    class EvenementCoaching {
        <<Événement>>
        +id : UUID
        +niveauUrgence : NiveauUrgence
        +scoreUrgence : Decimal
        +ecartPct : Decimal
        +ecartMontant : Decimal
        +previsionFinJournee : Decimal
        +conseil : str
        +produitAPousser : str
        +produitAEviter : str
        +strategie : str
        +causeRacine : str
        +statutGuardrail : str
        +scoreFeedback : int
        +futEfficace : bool
        +caApresCoaching : Decimal
    }

    Produit "1" --> "0..*" LigneDeVente
    Produit "1" --> "0..*" VenteTempsReel
    Produit "1" --> "0..*" HistoriqueVentes : agrégé dans
    LigneDeVente "*" --> "1" Conseiller
    LigneDeVente "*" --> "1" Boutique
    VenteTempsReel "*" --> "1" Conseiller
    VenteTempsReel "*" --> "1" Boutique
    Conseiller "1" --> "0..*" InteractionCoach : dialogue
    Conseiller "1" --> "0..*" EvenementCoaching : reçoit
    EvenementCoaching "*" ..> "0..*" ScriptDeVente : s'appuie sur
```

**Règles de gestion**

- `LigneDeVente` = historique consolidé (~1,93 M lignes) ; `VenteTempsReel` = flux
  chaud alimenté par la simulation temps réel, réconcilié en fin de journée.
- `HistoriqueVentes` est l'agrégat journalier `(date, boutique, sku)` servant de
  série temporelle d'entrée aux prévisions.
- `EvenementCoaching.futEfficace` / `caApresCoaching` ferment la boucle
  d'apprentissage : mesure a posteriori de l'impact du conseil délivré.

---

## 4. Sous-domaine « Stock & Approvisionnement »

```mermaid
classDiagram
    direction TB

    class NiveauDeStock {
        <<Entité>>
        +id : long
        +quantite : int
        +quantiteReservee : int
        +quantiteDisponible : int
        +joursDeStockRestants : float
        +derniereReception : date
        +derniereVente : date
        +majLe : datetime
        +estEnRupture() bool
        +estSousPointCommande() bool
        +couvertureJours() float
    }

    class MouvementDeStock {
        <<Événement>>
        +mouvementId : UUID
        +typeMouvement : TypeMouvement
        +quantite : int
        +stockAvant : int
        +stockApres : int
        +referenceId : str
        +referenceType : str
        +dateMouvement : datetime
        +notes : str
        +estEntrant() bool
    }

    class ParametreReappro {
        <<Référentiel>>
        +demandeMoyJour : Decimal
        +demandeStdJour : Decimal
        +leadTimeMoy : Decimal
        +leadTimeStd : Decimal
        +stockSecurite : int
        +pointCommande : int
        +eoq : int
        +niveauService : Decimal
        +joursStockCible : int
        +derniereMaj : datetime
        +recalculer(previsions) void
    }

    class Fournisseur {
        <<Référentiel>>
        +supplierId : str
        +nom : str
        +paysOrigine : str
        +typeFournisseur : str
        +categories : JSON
        +marques : JSON
        +delaiLivraisonMoy : int
        +delaiLivraisonStd : int
        +tauxFiabilite : Decimal
        +commandeMin : int
        +commandeMultiple : int
        +devise : str
        +conditionsPaiement : str
        +scoreGlobal : Decimal
        +actif : bool
        +delaiEstime(sku) int
    }

    class SourcingProduit {
        <<Référentiel>>
        +id : int
        +leadTimeDays : int
        +moq : int
        +coutUnitaire : Decimal
        +devise : str
        +estPrefere : bool
        +actif : bool
    }

    class BonDeCommande {
        <<Entité>>
        +poId : UUID
        +quantiteCommandee : int
        +quantiteRecue : int
        +prixUnitaireHT : Decimal
        +montantTotalHT : Decimal
        +devise : str
        +statut : StatutBC
        +priorite : str
        +source : SourceBC
        +urgence : str
        +confiance : Decimal
        +dateCommande : datetime
        +dateLivraisonPrevue : date
        +dateLivraisonReelle : date
        +delaiReelJours : int
        +livraisonConforme : bool
        +referenceExterne : str
        +avancer(nouveauStatut) void
        +receptionner(quantite) void
        +estEnRetard() bool
    }

    class NumeroDeSerie {
        <<Entité>>
        +id : UUID
        +numSerie : str
        +typeSerie : TypeSerie
        +statut : StatutSerie
        +dateReception : date
        +dateVente : date
        +numClient : str
    }

    class Transfert {
        <<Entité>>
        +transferId : UUID
        +storeSource : str
        +storeDest : str
        +quantite : int
        +statut : StatutTransfert
        +priorite : str
        +motif : str
        +dateDemande : datetime
        +dateApprobation : datetime
        +dateExpedition : datetime
        +dateReception : datetime
    }

    class DemandeReappro {
        <<Entité>>
        +requestId : str
        +sku : str
        +nomProduit : str
        +quantite : int
        +motif : str
        +urgence : UrgenceDemande
        +statut : StatutDemande
        +nomConseiller : str
        +noteManager : str
        +decidePar : str
        +decideLe : datetime
        +approuver(manager, note) void
        +rejeter(manager, note) void
    }

    class HistoriqueStock {
        <<Entité>>
        +recordDate : date
        +niveauStock : int
        +estRupture : bool
    }

    NiveauDeStock "*" --> "1" Produit
    NiveauDeStock "*" --> "1" Boutique
    MouvementDeStock "*" --> "1" Produit
    MouvementDeStock "*" --> "1" Boutique
    ParametreReappro "1" --> "1" Produit
    ParametreReappro "1" --> "1" Boutique
    Fournisseur "1" --> "0..*" SourcingProduit
    SourcingProduit "*" --> "1" Produit
    Fournisseur "1" --> "0..*" BonDeCommande
    BonDeCommande "*" --> "1" Produit
    BonDeCommande "*" --> "1" Boutique
    BonDeCommande "1" --> "0..*" NumeroDeSerie : réceptionne
    BonDeCommande "1" ..> "0..*" MouvementDeStock : RECEPTION_BC
    DemandeReappro "0..1" ..> "0..1" BonDeCommande
    DemandeReappro "*" --> "1" Utilisateur : émise par
    DemandeReappro "*" --> "1" Boutique
    Transfert "*" --> "1" Produit
    Transfert "*" --> "2" Boutique : source / destination
    HistoriqueStock "*" --> "1" Produit
    HistoriqueStock "*" --> "1" Boutique
    NiveauDeStock ..> ParametreReappro : comparé à
```

**Règles de gestion**

- `NiveauDeStock.quantiteDisponible` est une colonne **générée** :
  `quantite - quantiteReservee` — jamais écrite directement.
- Unicité `(sku, storeId)` sur `NiveauDeStock` et `ParametreReappro`.
- Un seul `SourcingProduit.estPrefere = true` par SKU (index unique partiel).
- Cycle de vie du `BonDeCommande` :
  `SUGGERE → BROUILLON → SOUMIS → CONFIRME → EXPEDIE → RECU_PARTIEL → RECU`
  (branches `ANNULE` / `LITIGE`). Le passage à `RECU` génère un `MouvementDeStock`
  de type `RECEPTION_BC` qui incrémente le `NiveauDeStock` — bouclage physique.
- `BonDeCommande.source = AGENT` ⇒ le BC provient d'une `Recommandation` et entre
  au statut `SUGGERE` : **aucune commande n'est émise sans validation humaine**.
- Un `Produit.serialisable = true` impose un `NumeroDeSerie` (IMEI/ICCID/eSIM) par
  unité réceptionnée puis vendue.

---

## 5. Sous-domaine « Prévision & Contexte marché »

```mermaid
classDiagram
    direction LR

    class PrevisionDemande {
        <<Entité>>
        +id : int
        +datePrevision : date
        +demande24h : Decimal
        +confianceBasse : Decimal
        +confianceHaute : Decimal
        +demandeBaseline : Decimal
        +demandeCorrigee : Decimal
        +methodeCorrection : str
        +featuresCorrection : JSON
        +versionModele : str
        +genereLe : datetime
        +intervalleConfiance() tuple
    }

    class PrecisionPrevision {
        <<Entité>>
        +id : int
        +datePrevision : date
        +demandeBaseline : Decimal
        +demandeCorrigee : Decimal
        +demandeReelle : Decimal
        +erreurBaseline : Decimal
        +erreurCorrigee : Decimal
        +wape() float
    }

    class AjustementContexte {
        <<Entité>>
        +id : int
        +upliftDemandePct : Decimal
        +sourceAjustement : str
        +impactMeteo : Decimal
        +impactPromo : Decimal
        +impactEvenement : Decimal
        +impactFerie : Decimal
        +signalDominant : str
        +signaux : JSON
        +confiance : Decimal
        +interpretation : str
        +valideDu : date
        +valideAu : date
        +estValide(date) bool
    }

    class Evenement {
        <<Référentiel>>
        +eventId : UUID
        +nom : str
        +typeEvenement : str
        +sousType : str
        +dateDebut : date
        +dateFin : date
        +portee : str
        +regionsIds : JSON
        +categoriesImpactees : JSON
        +upliftTerminal : Decimal
        +upliftForfait : Decimal
        +upliftSIM : Decimal
        +upliftRecharge : Decimal
        +intensite : str
        +noteStrategie : str
        +estEnCours(date) bool
        +upliftPour(categorie) Decimal
    }

    class Promotion {
        <<Référentiel>>
        +promoId : str
        +nom : str
        +dateDebut : date
        +dateFin : date
        +remisePct : Decimal
        +typePromo : str
        +portee : str
    }

    class MotifSaisonnier {
        <<Référentiel>>
        +categorie : str
        +mois : int
        +semaineMois : int
        +jourSemaine : int
    }

    class Concurrent {
        <<Référentiel>>
        +concurrentId : str
        +nom : str
        +codeOperateur : str
        +partMarchePct : Decimal
        +nbAbonnes : long
        +positionnement : str
        +pointsForts : JSON
        +pointsFaibles : JSON
    }

    class TarifConcurrent {
        <<Entité>>
        +categorie : str
        +produitType : str
        +donneesGo : Decimal
        +minutesVoix : int
        +prixTTC : Decimal
        +engagementMois : int
        +dateReleve : date
    }

    class FluxMNP {
        <<Entité>>
        +mnpId : UUID
        +direction : str
        +operateurOrigine : str
        +operateurDestination : str
        +mois : date
        +volume : int
        +raisonPrincipale : str
        +wilaya : str
    }

    class SegmentClient {
        <<Référentiel>>
        +segmentId : str
        +libelle : str
        +arpuMoyenTND : Decimal
        +tauxChurnBase : Decimal
        +dureeVieMois : int
        +produitsPreferes : JSON
        +poidsMarchePct : Decimal
    }

    PrevisionDemande "*" --> "1" Produit
    PrevisionDemande "*" --> "1" Boutique
    PrevisionDemande "1" --> "0..*" PrecisionPrevision : évaluée par
    AjustementContexte "*" ..> "1" PrevisionDemande : corrige
    AjustementContexte "*" --> "1" Produit
    AjustementContexte "*" --> "1" Boutique
    Evenement "0..*" ..> "0..*" AjustementContexte : signal
    Promotion "0..*" ..> "0..*" AjustementContexte : signal
    MotifSaisonnier "0..*" ..> "0..*" PrevisionDemande : saisonnalité
    Concurrent "1" --> "0..*" TarifConcurrent
    HistoriqueVentes "0..*" ..> "1" PrevisionDemande : entraîne
    PrevisionDemande ..> ParametreReappro : alimente
```

**Règles de gestion**

- Chaîne de prévision : `HistoriqueVentes` → **baseline MSTL** (`demandeBaseline`)
  → **correction XGBoost** (`demandeCorrigee`, `methodeCorrection`) →
  `PrecisionPrevision` mesure l'écart au réel (WAPE) a posteriori.
- Unicité `(sku, storeId, datePrevision)` sur `PrevisionDemande`.
- Unicité `(sku, storeId, valideDu)` sur `AjustementContexte` : un seul jeu de
  signaux contextuels par produit/boutique/jour.

---

## 6. Sous-domaine « Pilotage agentique & Décision »

Le cœur de la valeur métier : comment un écart commercial ou un risque de rupture
devient une action tracée, validée et mesurée.

```mermaid
classDiagram
    direction TB

    class CycleAgentique {
        <<Événement>>
        +id : int
        +cycleId : str
        +declenchePar : str
        +niveauUrgence : NiveauUrgence
        +scoreUrgence : float
        +ecartPct : float
        +ecartMontant : float
        +caJour : float
        +caCible : float
        +previsionFinJournee : float
        +syntheseAnalyste : str
        +strategie : str
        +nbActions : int
        +causeRacine : str
        +ragUtilise : bool
        +statut : str
        +dureeMs : float
        +estCritique() bool
    }

    class ExecutionAgent {
        <<Événement>>
        +id : int
        +nomAgent : str
        +demarreLe : datetime
        +termineLe : datetime
        +dureeMs : float
        +statut : str
        +itemsTraites : int
        +itemsReussis : int
        +itemsEchoues : int
        +alertesGenerees : int
        +recommandationsGenerees : int
    }

    class Alerte {
        <<Événement>>
        +id : int
        +typeAlerte : str
        +severite : str
        +message : str
        +actionRecommandee : str
        +statut : StatutAlerte
        +declencheeLe : datetime
        +resolueLe : datetime
        +acquitter(user) void
        +resoudre() void
    }

    class Recommandation {
        <<Entité>>
        +id : UUID
        +typeRecommandation : str
        +action : str
        +quantiteCommande : int
        +quantiteSuggeree : int
        +urgence : str
        +confiance : Decimal
        +texte : str
        +arbitrages : str
        +coutCommande : Decimal
        +coutPossession : Decimal
        +escaladeVersHumain : bool
        +motifEscalade : str
        +statut : StatutRecommandation
        +decidePar : str
        +decideLe : datetime
        +approuver(user) BonDeCommande
        +rejeter(user, motif) void
    }

    class RevueHumaine {
        <<Entité>>
        +id : UUID
        +niveauUrgence : NiveauUrgence
        +ecartPct : float
        +scoreCritique : float
        +feedbackCritique : str
        +syntheseStrategie : str
        +actions : JSON
        +source : str
        +statut : StatutRevue
        +approbateur : str
        +noteApprobateur : str
        +revuLe : datetime
    }

    class FeedbackAgent {
        <<Événement>>
        +id : int
        +source : str
        +refId : str
        +typeAction : str
        +decision : DecisionFeedback
        +motif : str
        +payload : JSON
    }

    class ObjectifMetier {
        <<Référentiel>>
        +id : int
        +typeObjectif : str
        +libelle : str
        +description : str
        +priorite : int
        +estActif : bool
    }

    CycleAgentique "1" --> "0..*" ExecutionAgent : orchestre
    ExecutionAgent "1" --> "0..*" Alerte : génère
    ExecutionAgent "1" --> "0..*" Recommandation : génère
    Alerte "1" --> "0..*" Recommandation : déclenche
    Alerte "*" --> "1" Produit
    Alerte "*" --> "1" Boutique
    Recommandation "*" --> "1" Produit
    Recommandation "*" --> "1" Boutique
    Recommandation "1" --> "0..1" BonDeCommande : matérialisée en
    Recommandation "0..1" --> "0..1" RevueHumaine : escalade
    CycleAgentique "1" --> "0..*" EvenementCoaching : produit
    RevueHumaine "1" --> "0..*" FeedbackAgent
    EvenementCoaching "1" --> "0..*" FeedbackAgent
    BonDeCommande "1" --> "0..*" FeedbackAgent
    ObjectifMetier "1" ..> "0..*" Recommandation : arbitre
    NiveauDeStock ..> Alerte : seuil franchi
    PrevisionDemande ..> Recommandation : dimensionne
```

**Chaîne causale complète** (tracée en base depuis la migration `0005`) :

```
LigneDeVente → MouvementDeStock(VENTE) → NiveauDeStock
      ↓
ExecutionAgent → Alerte(stockout_risk) → Recommandation(order_qty)
      ↓                                        ↓
RevueHumaine (si escaladeVersHumain)     BonDeCommande(SUGGERE)
      ↓                                        ↓
FeedbackAgent ←──────────────── MouvementDeStock(RECEPTION_BC) → NiveauDeStock
```

**Règles de gestion**

- Une `Recommandation` ne devient jamais un `BonDeCommande` exécuté sans passage
  par une décision humaine (`approuver()` ou `RevueHumaine`) — **porte HITL**.
- `Recommandation` : unicité d'une seule recommandation `pending` par
  `(sku, storeId)` (index unique partiel `uq_reco_pending_sku_store`).
- `ObjectifMetier.estActif` sélectionne l'arbitrage courant (ex. `balanced`,
  `minimize_stockout`, `minimize_cost`) qui pondère les recommandations.
- `FeedbackAgent` agrège les décisions humaines (suivi / ignoré / approuvé /
  rejeté) et est réinjecté dans les prompts des agents Décision et Stratège.

---

## 7. Énumérations métier

```mermaid
classDiagram
    class RoleUtilisateur {
        <<enumeration>>
        VENDEUR
        MANAGER
        ADMIN
    }
    class CanalVente {
        <<enumeration>>
        PHYSIQUE
        DIGITAL
        INDIRECT
    }
    class CycleDeVie {
        <<enumeration>>
        launch
        growth
        mature
        decline
        eol
    }
    class NiveauUrgence {
        <<enumeration>>
        CRITIQUE
        ELEVE
        MODERE
        OK
    }
    class NiveauCible {
        <<enumeration>>
        AGENT
        BOUTIQUE
        REGION
    }
    class StatutBC {
        <<enumeration>>
        SUGGERE
        BROUILLON
        SOUMIS
        CONFIRME
        EXPEDIE
        RECU_PARTIEL
        RECU
        ANNULE
        LITIGE
    }
    class SourceBC {
        <<enumeration>>
        AGENT
        MANUEL
    }
    class TypeMouvement {
        <<enumeration>>
        RECEPTION_BC
        VENTE
        RETOUR_CLIENT
        RETOUR_FOURNISSEUR
        TRANSFERT_ENTRANT
        TRANSFERT_SORTANT
        AJUSTEMENT_INVENTAIRE
        CASSE_PERTE
        INVENTAIRE_GAIN
        INVENTAIRE_PERTE
        RESERVATION
        LIBERATION_RESERVATION
    }
    class TypeSerie {
        <<enumeration>>
        IMEI
        ICCID
        ESIM
        EAN
    }
    class StatutSerie {
        <<enumeration>>
        EN_STOCK
        VENDU
        RESERVE
        DEFECTUEUX
        RETOURNE
        VOLE
        EN_TRANSIT
    }
    class StatutTransfert {
        <<enumeration>>
        DEMANDE
        APPROUVE
        EXPEDIE
        RECU
        REJETE
        ANNULE
        EN_LITIGE
    }
    class UrgenceDemande {
        <<enumeration>>
        RUPTURE
        CRITIQUE
        NORMALE
    }
    class StatutDemande {
        <<enumeration>>
        EN_ATTENTE
        APPROUVEE
        REJETEE
    }
    class StatutAlerte {
        <<enumeration>>
        pending
        acknowledged
        validated
        rejected
        dismissed
        resolved
    }
    class StatutRecommandation {
        <<enumeration>>
        pending
        approved
        rejected
        executed
        cancelled
    }
    class StatutRevue {
        <<enumeration>>
        pending
        approved
        rejected
    }
    class DecisionFeedback {
        <<enumeration>>
        followed
        ignored
        approved
        rejected
    }
```

---

## 8. Correspondance classe métier ↔ table PostgreSQL

| Classe métier | Table | Volume (2026-07) |
|---|---|---|
| `Boutique` | `sales.boutiques` | 201 |
| `Conseiller` | `sales.agents` | 699 |
| `Utilisateur` | `public.app_users` | 7 |
| `Objectif` | `sales.objectifs` | 23 517 |
| `CibleMensuelle` | `public.telco_targets_monthly` | 6 917 |
| `KpiConseillerJour` | `public.agent_kpi_daily` | 114 211 |
| `ScriptDeVente` | `sales.coaching_scripts` | 1 141 |
| `SatisfactionClient` | `customer.nps_csat` | — |
| `Produit` | `sales.produits` (+ `inventory.product_master`) | 4 593 |
| `LigneDeVente` | `sales.transactions` | 1 929 823 |
| `VenteTempsReel` | `sales.transactions_rt` | 8 238 |
| `HistoriqueVentes` | `inventory.sales_history` | 693 954 |
| `InteractionCoach` | `public.coach_interactions` | 255 |
| `EvenementCoaching` | `coaching.coaching_events` | — |
| `NiveauDeStock` | `inventory.stock_levels` | 46 244 |
| `HistoriqueStock` | `inventory.stock_history` | 844 987 |
| `MouvementDeStock` | `supply.stock_movements` | 156 669 |
| `ParametreReappro` | `supply.reorder_params` | 945 |
| `Fournisseur` | `supply.suppliers` | 10 |
| `SourcingProduit` | `supply.supplier_products` | — |
| `BonDeCommande` | `supply.purchase_orders` | — |
| `NumeroDeSerie` | `supply.serial_numbers` | 177 |
| `Transfert` | `supply.transfers` | 34 |
| `DemandeReappro` | `public.product_requests` | — |
| `PrevisionDemande` | `inventory.demand_forecast` | 840 |
| `PrecisionPrevision` | `inventory.forecast_accuracy` | — |
| `AjustementContexte` | `inventory.context_adjustments` | 1 907 |
| `Evenement` | `market.events` (+ `inventory.events`) | — |
| `Promotion` | `inventory.promotions` | 27 |
| `MotifSaisonnier` | `market.seasonal_patterns` | — |
| `Concurrent` / `TarifConcurrent` | `market.competitors` / `market.competitor_pricing` | — |
| `FluxMNP` | `market.mnp_flows` | — |
| `SegmentClient` | `customer.segments` | — |
| `CycleAgentique` | `public.agent_cycles` | 1 147 |
| `ExecutionAgent` | `inventory.agent_runs` | 63 309 |
| `Alerte` | `inventory.alerts` | 144 |
| `Recommandation` | `inventory.recommendations` | 511 |
| `RevueHumaine` | `public.hitl_reviews` | 9 |
| `FeedbackAgent` | `public.agent_feedback` | — |
| `ObjectifMetier` | `inventory.business_objectives` | 6 |

> Les tables purement techniques (`agent_logs`, `agent_errors`, `agent_memory`,
> `app_sessions`, `rag_queries`, `rag_feedback`, `alembic_version`) sont exclues :
> elles relèvent de l'observabilité et de l'infrastructure, pas du domaine métier.

---

## 9. Pivots du modèle

Deux entités structurent tout le domaine et servent de clé de jointure quasi
universelle :

- **`Boutique` (`store_id`)** — référencée par 11 tables. Unité de pilotage :
  objectifs, stock, alertes, recommandations, BC, RBAC.
- **`Produit` (`sku`)** — référencée par 9 tables. Unité de décision :
  vente, stock, prévision, réappro, sourcing.

Le couple **`(sku, store_id)`** est la granularité de décision du système :
c'est à ce niveau que sont calculés le point de commande, la prévision de
demande, l'ajustement contextuel et la recommandation d'achat.
