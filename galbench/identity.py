"""Model identity provenance, without inferring a model from its Agent client."""

def model_identity(summary, settings):
    declared = str(summary.get("model") or settings.get("name") or "未知模型")
    reported = sorted({v.strip() for v in summary.get("returned_models", [])
                       if isinstance(v, str) and v.strip() and v.casefold() not in {"none", "null", "unknown"}})
    if len(reported) == 1:
        return {"name":reported[0], "declared":declared, "reported":reported, "source":"reported"}
    if len(reported) > 1:
        return {"name":declared, "declared":declared, "reported":reported, "source":"mixed"}
    tier_only = settings.get("provider")=="qoder" and declared.casefold() in {"auto","lite","efficient","performance","ultimate","fast","premium"}
    return {"name":declared, "declared":declared, "reported":[],
            "source":"unknown" if declared.endswith("/client-default") or tier_only else "declared"}
