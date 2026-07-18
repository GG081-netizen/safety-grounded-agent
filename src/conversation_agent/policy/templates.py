"""Public, business-domain policy response templates."""

from conversation_agent.policy.models import PolicyDecision


def blocked_message(decision: PolicyDecision) -> str:
    del decision
    return "该请求超出当前业务支持范围。我可以帮助改写为基于已验证资料、权限和合规边界的方案。"


def uncertain_message(decision: PolicyDecision) -> str:
    del decision
    return "该请求需要进一步证据、授权或专业人员确认；我可以先协助整理待确认事项。"
