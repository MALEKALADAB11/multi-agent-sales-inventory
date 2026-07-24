# Prompts de génération d'images — 4 vues d'architecture

Un fichier = une image. Copier le **Prompt A** du fichier voulu, le coller dans
un LLM générateur de code, récupérer le SVG.

| # | Fichier | Image produite | Question à laquelle elle répond |
|---|---|---|---|
| 1 | [01_GLOBAL_ARCHITECTURE.md](01_GLOBAL_ARCHITECTURE.md) | **Global Architecture** | Que fait le système, avec qui dialogue-t-il ? |
| 2 | [02_LOGICAL_ARCHITECTURE.md](02_LOGICAL_ARCHITECTURE.md) | **Logical Architecture** | Comment les responsabilités sont-elles découpées ? |
| 3 | [03_PHYSICAL_ARCHITECTURE.md](03_PHYSICAL_ARCHITECTURE.md) | **Physical Architecture** | Où tourne quoi, sur quel port ? |
| 4 | [04_COMPONENT_DIAGRAM.md](04_COMPONENT_DIAGRAM.md) | **Component Diagram** | Qui dépend de quoi, par quel contrat ? |
| 5 | [05_MULTI_AGENT_ARCHITECTURE.md](05_MULTI_AGENT_ARCHITECTURE.md) | **Multi-Agent Architecture** | Que consomme et produit chaque agent, et comment se passent-ils le relais ? |
| 6 | [06_FLUX_ANALYSTE_STRATEGE.md](06_FLUX_ANALYSTE_STRATEGE.md) | **Flux Analyste → Stratège** | Concrètement, quelles données, quels prompts et quels JSON circulent d'un agent à l'autre ? |

## Structure identique dans chaque fichier

1. **Prompt A — SVG exact** ⭐ pour un LLM générateur de code. C'est celui à
   utiliser pour le rapport : texte net, vectoriel, éditable, imprimable.
2. **Prompt B — Générateur d'images** (Midjourney, DALL·E, Flux, Imagen) pour
   une slide, avec son prompt négatif. Le texte y sera illisible : usage
   décoratif uniquement.
3. **Checklist de vérification** propre à la vue, à passer avant d'intégrer
   l'image au mémoire.

## Cohérence entre les quatre images

Les quatre prompts partagent la **même charte** — palette sémantique identique,
mêmes formes, mêmes conventions de trait. Générés séparément, ils se lisent
comme une seule série.

Règle de couleur invariante : le **rouge Ooredoo `#E30613` est réservé au
domaine Vente** (et à la boîte système dans la vue globale). Il n'apparaît
nulle part ailleurs.

## Ne pas mélanger les vues

L'erreur la plus fréquente en soutenance est de faire fuiter une vue dans une
autre. Les checklists sont là pour ça :

- Vue **logique** : aucun port, aucun conteneur, aucun chemin de fichier.
- Vue **physique** : aucun nom d'agent, aucun node LangGraph.
- Vue **globale** : aucun détail interne.
- **Composants** : chaque connecteur porte un nom d'interface, sinon ce n'est
  qu'un organigramme.

## Sources

- Contenu des quatre premières vues : [../VUES_ARCHITECTURE.md](../VUES_ARCHITECTURE.md)
- Contrats d'E/S des agents et orchestration :
  [../ARCHITECTURE_MULTI_AGENTS.md](../ARCHITECTURE_MULTI_AGENTS.md)
- Spécification détaillée et charte graphique complète :
  [../ARCHITECTURE_GLOBALE_VISUELLE.md](../ARCHITECTURE_GLOBALE_VISUELLE.md) §14
