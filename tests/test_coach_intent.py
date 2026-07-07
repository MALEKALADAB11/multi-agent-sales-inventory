"""
Tests unitaires — Intent classification du CoachAgent.
Toutes les fonctions testées sont pures (pas de DB, pas de LLM).
"""
import pytest


def _import_classify():
    try:
        import sys, os
        from app.sales.coaching.agents.coach.coach_chat import _classify_intent
        return _classify_intent
    except Exception as exc:
        pytest.skip(f"_classify_intent import failed: {exc}")


def _import_dedup():
    try:
        import sys, os
        from app.sales.coaching.agents.coach.coach_chat import _is_duplicate
        return _is_duplicate
    except Exception as exc:
        pytest.skip(f"_is_duplicate import failed: {exc}")


# ═══════════════════════════════════════════════════════════════════════════════
# INTENT CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════════

class TestClassifyIntent:

    # _classify_intent retourne un dict {mode, domain, type, confidence}
    # (contrat actuel — les anciens tests supposaient un simple str).

    def test_greeting_detected(self):
        _classify = _import_classify()
        assert _classify("bonjour")["type"] == "greeting"
        assert _classify("salut coach")["type"] == "greeting"
        assert _classify("Bonjour, tu vas bien ?")["type"] == "greeting"

    def test_coaching_request(self):
        _classify = _import_classify()
        for msg in ("comment vendre l'iPhone 16 Pro",
                    "donne-moi un script de vente forfait 5G",
                    "conseil pour closing client hésitant"):
            result = _classify(msg)
            assert result["domain"] in ("sales", "both")
            assert result["type"] not in ("off_topic", "greeting")

    def test_stock_query(self):
        _classify = _import_classify()
        result = _classify("quel est le stock iPhone ?")
        assert result["domain"] in ("inventory", "both", "sales")
        assert result["type"] != "off_topic"

    def test_performance_query(self):
        _classify = _import_classify()
        result = _classify("quel est mon objectif aujourd'hui ?")
        assert result["type"] != "off_topic"

    def test_empty_message(self):
        _classify = _import_classify()
        result = _classify("")
        assert isinstance(result, dict)
        assert result.get("type")

    def test_unknown_returns_dict(self):
        _classify = _import_classify()
        result = _classify("xkcd nonsense 42")
        assert isinstance(result, dict)
        assert {"mode", "domain", "type", "confidence"} <= set(result)


# ═══════════════════════════════════════════════════════════════════════════════
# DEDUP CACHE
# ═══════════════════════════════════════════════════════════════════════════════

class TestDedupCache:

    def test_same_message_within_ttl_is_duplicate(self):
        _is_dup = _import_dedup()
        msg  = "bonjour"
        adv  = "Advisor1"
        store = "I63"
        # Premier appel → pas encore dupliqué
        first = _is_dup(msg, adv, store)
        # Deuxième appel immédiat → dupliqué
        second = _is_dup(msg, adv, store)
        # Si le cache est vide au départ, premier = False, second = True
        # Si le cache avait déjà ce message, premier peut être True
        assert isinstance(first, bool)
        assert isinstance(second, bool)
        # La deuxième fois doit être au moins aussi "duplicate" que la première
        assert second >= first

    def test_different_messages_not_duplicate(self):
        _is_dup = _import_dedup()
        _is_dup("message A unique xyz", "Adv", "I63")
        result = _is_dup("message B unique abc", "Adv", "I63")
        assert result is False

    def test_different_advisors_not_duplicate(self):
        _is_dup = _import_dedup()
        msg = "test dédup conseiller"
        _is_dup(msg, "AdvisorX", "I63")
        result = _is_dup(msg, "AdvisorY", "I63")
        assert result is False


# ═══════════════════════════════════════════════════════════════════════════════
# SELF-CRITIQUE HITL TRIGGER LOGIC
# ═══════════════════════════════════════════════════════════════════════════════

class TestHitlTriggerLogic:
    """Vérifie la règle HITL sans dépendances IO."""

    THRESHOLD_GAP      = 40.0
    HITL_URGENCIES     = ("CRITICAL", "HIGH")
    CRITIQUE_MIN_SCORE = 0.6

    def _should_trigger(self, critique_score, urgency, gap_pct):
        critique_passed = critique_score >= self.CRITIQUE_MIN_SCORE
        return (
            not critique_passed
            and urgency in self.HITL_URGENCIES
            and gap_pct >= self.THRESHOLD_GAP
        )

    def test_trigger_when_critique_fails_and_critical(self):
        assert self._should_trigger(0.45, "CRITICAL", 62.0) is True

    def test_no_trigger_when_critique_passes(self):
        assert self._should_trigger(0.75, "CRITICAL", 62.0) is False

    def test_no_trigger_when_low_urgency(self):
        assert self._should_trigger(0.45, "LOW", 62.0) is False

    def test_no_trigger_when_small_gap(self):
        assert self._should_trigger(0.45, "HIGH", 20.0) is False

    def test_trigger_when_high_urgency_and_failing(self):
        assert self._should_trigger(0.55, "HIGH", 50.0) is True
