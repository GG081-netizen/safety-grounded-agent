"""CLI entry point — Click-based command-line interface.

Commands:
    chat        Interactive conversation with the agent
    customer    Customer CRUD (list, show, search)
    seed        Generate seed data
"""

from __future__ import annotations

import json
import logging
import sys
import asyncio
from pathlib import Path

import click
from sqlalchemy import text

from conversation_agent.agent import Agent, MockAgent, RealAgent
from conversation_agent.orchestration.coordinator import Coordinator
from conversation_agent.config import get_config
from conversation_agent.evaluation import (
    evaluate_policy_boundary,
    evaluate_production_blockers,
    evaluate_rag_adapter,
    exit_code_for_status,
)
from conversation_agent.logging_config import setup_logging
from conversation_agent.memory.customer_store import CustomerStore
from conversation_agent.sales.models import CustomerProfile
from conversation_agent.database.engine import DatabaseEngine
from conversation_agent.operations import (
    IdempotencyPruner,
    PersistenceDoctor,
    PersistenceIntegrityChecker,
    audit_production_config,
)


# ── Common options ────────────────────────────────────────────────────────────

def _setup_context(ctx: click.Context, verbose: bool) -> None:
    """Apply --verbose and store objects on context."""
    level = "DEBUG" if verbose else "INFO"
    setup_logging(level=level, jsonl_enabled=False)
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    ctx.obj["store"] = CustomerStore()
    ctx.obj["agent"] = Coordinator()


def _output(data, ctx: click.Context, formatter=None) -> None:
    """Print data as JSON if --json, otherwise human-readable."""
    if ctx.obj.get("json_mode"):
        if formatter:
            data = formatter(data)
        click.echo(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    else:
        if isinstance(data, str):
            click.echo(data)
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    click.echo(_format_dict(item))
                else:
                    click.echo(str(item))
        elif isinstance(data, dict):
            click.echo(_format_dict(data))
        else:
            click.echo(str(data))




def _orchestration_to_public_dict(result) -> dict:
    """Dump orchestration output while respecting raw RAG response policy."""
    cfg = get_config()
    return result.to_public_dict(
        include_raw_response=cfg.rag_service.include_raw_response
    )

def _format_dict(d: dict, indent: str = "") -> str:
    """Pretty-print a dict as key: value lines."""
    lines = []
    for k, v in d.items():
        if v is None:
            lines.append(f"{indent}{k}: -")
        elif isinstance(v, list):
            lines.append(f"{indent}{k}: {', '.join(str(x) for x in v)}")
        elif isinstance(v, dict):
            lines.append(f"{indent}{k}:")
            lines.append(_format_dict(v, indent + "  "))
        else:
            lines.append(f"{indent}{k}: {v}")
    return "\n".join(lines)


# ── Global options ────────────────────────────────────────────────────────────

@click.group()
@click.option("--json", "json_mode", is_flag=True, help="以 JSON 格式输出")
@click.option("--verbose", "-v", is_flag=True, help="详细输出（DEBUG 级别日志）")
@click.option("--stdin", "read_stdin", is_flag=True, help="从标准输入读取用户输入")
@click.pass_context
def cli(ctx: click.Context, json_mode: bool, verbose: bool, read_stdin: bool) -> None:
    """Procurement Sales Copilot Agent — 企业采购销售智能助手."""
    _setup_context(ctx, verbose)
    ctx.obj["json_mode"] = json_mode
    ctx.obj["read_stdin"] = read_stdin


# ── chat ──────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("text", required=False)
@click.pass_context
def chat(ctx: click.Context, text: str | None) -> None:
    """与 Agent 对话。如果不提供 text，从 stdin 读取。"""
    agent: Coordinator = ctx.obj["agent"]

    if ctx.obj["read_stdin"] or not text:
        if not text:
            text = sys.stdin.read().strip()
        if not text:
            click.echo("错误：未提供输入。请传递文本或使用 --stdin。", err=True)
            sys.exit(1)

    result = agent.run(text)

    if ctx.obj["json_mode"]:
        data = _orchestration_to_public_dict(result)
        data["agent_response"] = result.final_response
        click.echo(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    else:
        if result.intent_result:
            intent_label = result.intent_result.confidence_label
            click.secho(
                f"意图: {result.intent_result.intent.value} "
                f"({intent_label}, {result.intent_result.confidence:.2f})",
                fg="cyan",
            )
            if result.intent_result.reasoning:
                click.echo(f"  推理: {result.intent_result.reasoning}")
        if result.task_route:
            click.echo(f"任务: {result.task_route.task} ({result.task_route.confidence:.2f})")
        click.echo(f"安全: {result.policy.status} - {result.policy.reason}")

        click.echo()
        for step in result.trace:
            click.secho(f"  ok {step.step_name}: {step.output_summary}", fg="green")
            for w in step.warnings:
                click.secho(f"    ! {w}", fg="yellow")

        click.echo()
        click.secho("Agent 回复:", bold=True)
        click.echo(result.final_response)

        if result.metadata.latency_ms > 0:
            click.echo()
            click.secho(
                f"模式: Coordinator | 延迟: {result.metadata.latency_ms}ms | "
                f"置信度: {result.confidence:.2f}",
                dim=True,
            )


# ── qa ────────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("text", required=False)
@click.pass_context
def qa(ctx: click.Context, text: str | None) -> None:
    """企业知识问答，输出引用和置信度。"""
    agent: Coordinator = ctx.obj["agent"]
    if ctx.obj["read_stdin"] or not text:
        if not text:
            text = sys.stdin.read().strip()
        if not text:
            click.echo("错误：未提供输入。请传递文本或使用 --stdin。", err=True)
            sys.exit(1)
    result = agent.run(text, task_override="qa")
    if ctx.obj["json_mode"]:
        click.echo(json.dumps(_orchestration_to_public_dict(result), ensure_ascii=False, indent=2, default=str))
        return
    click.echo(result.final_response)


# ── report ────────────────────────────────────────────────────────────────────

@cli.group()
def report() -> None:
    """报告生成。"""
    pass


@report.command("weekly")
@click.pass_context
def report_weekly(ctx: click.Context) -> None:
    """生成本周销售周报。"""
    agent: Coordinator = ctx.obj["agent"]
    result = agent.run("生成本周销售周报", task_override="weekly_report")
    if ctx.obj["json_mode"]:
        click.echo(json.dumps(_orchestration_to_public_dict(result), ensure_ascii=False, indent=2, default=str))
        return
    click.echo(result.final_response)


# ── eval ──────────────────────────────────────────────────────────────────────

@cli.group("eval")
def eval_group() -> None:
    """项目评测与发布验收。"""
    pass


@eval_group.command("rag-adapter")
@click.option("--strict", is_flag=True, help="按四态评测状态返回非零退出码")
@click.pass_context
def eval_rag_adapter(ctx: click.Context, strict: bool) -> None:
    """评估 Agent 层调用外部 RAG / fallback / safety 的可靠性。"""
    include_raw = get_config().rag_service.include_raw_response
    report = evaluate_rag_adapter(include_raw_response=include_raw)
    if ctx.obj["json_mode"]:
        click.echo(json.dumps(report.to_public_dict(), ensure_ascii=False, indent=2, default=str))
        if strict and report.summary.status != "pass":
            ctx.exit(exit_code_for_status(report.summary.status))
        return

    summary = report.summary
    click.secho("RAG Adapter Evaluation", bold=True)
    click.echo()
    click.echo(f"Cases: {summary.case_count}")
    click.echo(f"External success rate: {summary.external_success_rate:.2f}")
    click.echo(f"Fallback rate: {summary.fallback_rate:.2f}")
    click.echo(f"Citation coverage: {summary.citation_coverage:.2f}")
    click.echo(f"No evidence rate: {summary.no_evidence_rate:.2f}")
    click.echo(f"Average confidence: {summary.average_confidence:.2f}")
    click.echo(f"Blocked no-RAG-call rate: {summary.blocked_no_rag_call_rate:.2f}")
    click.echo(f"Raw response exposure rate: {summary.raw_response_exposure_rate:.2f}")
    click.echo()
    click.echo(f"Status: {summary.status.upper()}")
    for message in summary.gate_messages:
        click.echo(f"- {message}")
    if strict and summary.status != "pass":
        ctx.exit(exit_code_for_status(summary.status))


@eval_group.command("policy-boundary")
@click.option("--strict", is_flag=True, help="按四态评测状态返回非零退出码")
@click.pass_context
def eval_policy_boundary(ctx: click.Context, strict: bool) -> None:
    """评估企业销售/售前业务安全边界。"""
    report = evaluate_policy_boundary()
    if ctx.obj["json_mode"]:
        click.echo(json.dumps(report.to_public_dict(), ensure_ascii=False, indent=2, default=str))
        if strict and report.summary.status != "pass":
            ctx.exit(exit_code_for_status(report.summary.status))
        return

    summary = report.summary
    click.secho("Policy Boundary Evaluation", bold=True)
    click.echo()
    click.echo(f"Cases: {summary.case_count}")
    click.echo(f"Blocked detection rate: {summary.blocked_detection_rate:.2f}")
    click.echo(f"Uncertain detection rate: {summary.uncertain_detection_rate:.2f}")
    click.echo(f"Safe pass rate: {summary.safe_pass_rate:.2f}")
    click.echo(f"Blocked no-RAG-call rate: {summary.blocked_no_rag_call_rate:.2f}")
    click.echo(f"Business boundary coverage: {summary.business_boundary_coverage}")
    click.echo(f"Covered categories: {', '.join(summary.covered_categories)}")
    click.echo()
    click.echo(f"Status: {summary.status.upper()}")
    for message in summary.gate_messages:
        click.echo(f"- {message}")
    if strict and summary.status != "pass":
        ctx.exit(exit_code_for_status(summary.status))


@eval_group.command("production-blockers")
@click.option(
    "--scope",
    type=click.Choice(
        ("implementation", "incident-evidence", "phase"),
        case_sensitive=False,
    ),
    default="phase",
    show_default=True,
)
@click.option("--strict", is_flag=True, help="按四态评测状态返回非零退出码")
@click.pass_context
def eval_production_blockers(ctx: click.Context, scope: str, strict: bool) -> None:
    """评估 Phase 14 技术修复与事故关闭条件。"""
    report = evaluate_production_blockers(scope=scope)
    payload = report.to_public_dict()
    if ctx.obj["json_mode"]:
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        click.secho("Production Blockers Evaluation", bold=True)
        click.echo(f"Scope: {scope}")
        click.echo(f"Implementation: {report.summary.implementation_status.upper()}")
        if report.summary.incident_evidence_status is not None:
            click.echo(
                "Incident evidence: "
                f"{report.summary.incident_evidence_status.upper()}"
            )
            click.echo(
                f"Phase candidate: {report.summary.phase_candidate_status.upper()}"
            )
        if report.summary.incident_closure_status is not None:
            click.echo(
                "Incident closure: "
                f"{report.summary.incident_closure_status.upper()}"
            )
        if report.summary.phase_status is not None:
            click.echo(f"Phase: {report.summary.phase_status.upper()}")
        click.echo(f"Authoritative: {str(report.summary.authoritative).lower()}")
        for reason in report.summary.blocking_reasons:
            click.echo(f"- {reason}")
    strict_status = {
        "implementation": report.summary.implementation_status,
        "incident-evidence": report.summary.incident_evidence_status,
        "phase": report.summary.phase_status,
    }[scope]
    if strict and strict_status != "pass":
        ctx.exit(exit_code_for_status(strict_status or "blocked"))


# ── persistence operations ───────────────────────────────────────────────────

@cli.group("ops")
def operations_group() -> None:
    """有界、脱敏的持久化运维命令。"""
    pass


async def _with_database(operation):
    config = get_config()
    engine = DatabaseEngine(config.database)
    await engine.start()
    try:
        return await operation(config, engine)
    finally:
        await engine.stop()


@operations_group.command("persistence-doctor")
@click.option("--full", is_flag=True, help="显式执行有总超时的完整检查")
@click.pass_context
def persistence_doctor(ctx: click.Context, full: bool) -> None:
    """只读检查连接、版本、数据库时间和持久化不变量。"""

    async def run(config, engine):
        return await PersistenceDoctor(
            config=config,
            engine=engine.engine,
        ).run(full=full)

    try:
        report = asyncio.run(_with_database(run))
    except Exception:
        click.echo("persistence doctor could not establish a safe diagnostic context", err=True)
        ctx.exit(3)
    payload = report.to_dict()
    _output(payload, ctx)
    if report.exit_code:
        ctx.exit(report.exit_code)


@operations_group.command("persistence-integrity")
@click.option("--full", is_flag=True, help="显式执行有总超时的完整检查")
@click.pass_context
def persistence_integrity(ctx: click.Context, full: bool) -> None:
    """只读检查 Request、Run、Audit 和幂等状态。"""

    async def run(config, engine):
        del config
        return await PersistenceIntegrityChecker(engine.engine).check(full=full)

    try:
        report = asyncio.run(_with_database(run))
    except Exception:
        click.echo("persistence integrity check is unavailable", err=True)
        ctx.exit(3)
    _output(report.to_dict(), ctx)
    if report.status != "healthy" or not report.complete:
        ctx.exit(5)


@operations_group.command("idempotency-prune")
@click.option("--apply", is_flag=True, help="实际删除符合条件的终态幂等记录")
@click.option("--confirm-database", default="", help="确认目标数据库名")
@click.option("--confirm-environment", default="", help="确认当前运行环境")
@click.option("--batch-size", type=click.IntRange(1, 1000), default=100)
@click.option("--safety-margin-seconds", type=click.IntRange(1), default=300)
@click.pass_context
def idempotency_prune(
    ctx: click.Context,
    apply: bool,
    confirm_database: str,
    confirm_environment: str,
    batch_size: int,
    safety_margin_seconds: int,
) -> None:
    """默认 dry-run；实际清理要求数据库和环境双重确认。"""

    async def run(config, engine):
        if apply:
            async with engine.engine.connect() as connection:
                database_name = await connection.scalar(
                    text("SELECT current_database()")
                )
            if confirm_database != database_name:
                raise ValueError("database confirmation does not match")
            if confirm_environment != config.runtime_mode:
                raise ValueError("environment confirmation does not match")
        pruner = IdempotencyPruner(
            engine.engine,
            batch_size=batch_size,
            safety_margin_seconds=safety_margin_seconds,
        )
        return await pruner.run(apply=apply)

    try:
        report = asyncio.run(_with_database(run))
    except ValueError as exc:
        raise click.UsageError(str(exc)) from None
    except Exception:
        click.echo("idempotency prune could not complete safely", err=True)
        ctx.exit(3)
    _output(report.to_dict(), ctx)


@operations_group.command("production-config-audit")
@click.pass_context
def production_config_audit(ctx: click.Context) -> None:
    """静态检查生产持久化配置，不建立数据库连接。"""

    report = audit_production_config(get_config())
    _output(report.to_dict(), ctx)
    if report.status != "healthy":
        ctx.exit(2)


# ── customer ──────────────────────────────────────────────────────────────────

@cli.group()
def customer() -> None:
    """客户管理（list, show, search）."""
    pass


@customer.command("list")
@click.pass_context
def customer_list(ctx: click.Context) -> None:
    """列出所有客户."""
    store: CustomerStore = ctx.obj["store"]
    profiles = store.list_all()

    if ctx.obj["json_mode"]:
        data = [
            {
                "customer_id": p.customer_id,
                "customer_name": p.customer_name,
                "industry": p.industry,
                "sales_stage": p.sales_stage.value,
                "status": p.status.value,
                "deal_score": p.deal_score.score if p.deal_score else None,
                "health_score": p.health_score.health_score if p.health_score else None,
            }
            for p in profiles
        ]
        click.echo(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    else:
        if not profiles:
            click.echo("暂无客户数据。使用 'seed' 命令生成测试数据。")
            return
        for p in profiles:
            ds = f"{p.deal_score.score}({p.deal_score.level.value})" if p.deal_score else "-"
            hs = f"{p.health_score.health_score}({p.health_score.status.value})" if p.health_score else "-"
            click.echo(
                f"{p.customer_id:<12} {p.customer_name:<16} "
                f"阶段: {p.sales_stage.value:<22} "
                f"成交: {ds:<8} 健康: {hs:<8}"
            )


@customer.command("show")
@click.argument("identifier")
@click.pass_context
def customer_show(ctx: click.Context, identifier: str) -> None:
    """查看客户详情（按名称或 ID）."""
    store: CustomerStore = ctx.obj["store"]

    profile = store.load(identifier) or store.find_by_name(identifier)
    if profile is None:
        click.echo(f"未找到客户: {identifier}", err=True)
        sys.exit(1)

    if ctx.obj["json_mode"]:
        click.echo(profile.model_dump_json(indent=2))
    else:
        click.secho(f"{profile.customer_name} ({profile.customer_id})", bold=True)
        click.echo(f"  行业: {profile.industry or '-'}")
        click.echo(f"  规模: {profile.company_size or '-'}")
        click.echo(f"  阶段: {profile.sales_stage.value}")
        click.echo(f"  状态: {profile.status.value}")
        click.echo(f"  联系人: {profile.contact_count}人"
                    + (f"（含决策人）" if profile.has_decision_maker_contact else ""))
        if profile.procurement_items:
            click.echo(f"  采购项: {profile.item_count}项")
            for item in profile.procurement_items:
                click.echo(f"    - {item.product_name} ×{item.quantity or '?'}")
        if profile.deal_score:
            click.echo(f"  成交评分: {profile.deal_score.score}"
                        f"({profile.deal_score.level.value}, "
                        f"{profile.deal_score.confidence})")
        if profile.health_score:
            click.echo(f"  健康度: {profile.health_score.health_score}"
                        f"({profile.health_score.status.value})")
        if profile.risks:
            click.echo(f"  风险: {len(profile.risks)}项")
            for r in profile.risks:
                click.echo(f"    [{r.level.value}] {r.reason}")
        click.echo(f"  版本: v{profile.version} (schema v{profile.schema_version})")


@customer.command("search")
@click.option("--name", "-n", default=None, help="客户名称（模糊匹配）")
@click.option("--industry", "-i", default=None, help="行业")
@click.option("--stage", "-s", default=None, help="销售阶段")
@click.option("--status", default=None, help="客户状态")
@click.pass_context
def customer_search(
    ctx: click.Context,
    name: str | None,
    industry: str | None,
    stage: str | None,
    status: str | None,
) -> None:
    """搜索客户."""
    store: CustomerStore = ctx.obj["store"]
    results = store.search(
        customer_name=name,
        industry=industry,
        sales_stage=stage,
        status=status,
    )

    if ctx.obj["json_mode"]:
        data = [
            {
                "customer_id": p.customer_id,
                "customer_name": p.customer_name,
                "industry": p.industry,
                "sales_stage": p.sales_stage.value,
                "status": p.status.value,
            }
            for p in results
        ]
        click.echo(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    else:
        click.echo(f"找到 {len(results)} 个客户:")
        for p in results:
            click.echo(f"  {p.customer_id}  {p.customer_name}  "
                        f"{p.sales_stage.value}  {p.industry or '-'}")


# ── seed ──────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--count", "-c", default=5, help="生成的客户数量（默认 5）")
@click.option("--clear/--no-clear", default=False, help="清空现有数据")
@click.pass_context
def seed(ctx: click.Context, count: int, clear: bool) -> None:
    """生成种子数据用于开发和演示."""
    from conversation_agent.cli.seed import seed as run_seed

    n = run_seed(count=count, clear_first=clear)
    click.echo(f"已生成 {n} 个客户（含互动记录）")
