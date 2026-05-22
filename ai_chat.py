def ai_response(question, risk_cache):
    """
    Simple StableGuard AI assistant.
    """

    q = question.lower()

    # Basic system overview
    if "safest" in q:

        safest = None
        safest_score = 999

        for symbol, data in risk_cache.items():

            score = data.get(
                "composite_score",
                0
            )

            if score < safest_score:
                safest_score = score
                safest = symbol

        return (
            f"{safest} currently appears "
            f"to be the safest monitored "
            f"stablecoin with risk score "
            f"{safest_score}."
        )

    # DAI explanation
    if "dai" in q:

        dai = risk_cache.get("DAI")

        if not dai:
            return "DAI data unavailable."

        return (
            f"DAI currently has alert level "
            f"{dai.get('alert_level')} "
            f"with composite risk score "
            f"{dai.get('composite_score')}."
        )

    # USDT explanation
    if "usdt" in q:

        usdt = risk_cache.get("USDT")

        if not usdt:
            return "USDT data unavailable."

        return (
            f"USDT currently has alert level "
            f"{usdt.get('alert_level')} "
            f"with composite risk score "
            f"{usdt.get('composite_score')}."
        )

    # General market question
    if "market" in q or "overview" in q:

        risky = [
            s for s, d in risk_cache.items()
            if d.get("alert_level") != "🟢 HEALTHY"
        ]

        if risky:

            return (
                "Current market conditions "
                "show elevated risk in: "
                + ", ".join(risky)
            )

        return (
            "Stablecoin market conditions "
            "currently appear stable."
        )

    return (
        "I can help explain stablecoin "
        "risk conditions, alerts, and "
        "market stability."
    )