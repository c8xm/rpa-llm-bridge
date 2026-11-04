"""执行器测试：成功、失败、重试、人工闸门暂停与续跑、dry-run。"""

from __future__ import annotations

from tests.conftest import FakeDriver, make_step

from agentrpa.core.runner import RecipeRunner


def recipe(*steps, name: str = "t", vars_: dict | None = None):
    return {"name": name, "driver": "browser", "vars": vars_ or {}, "steps": list(steps)}


def build(driver, policy, audit, settings):
    return RecipeRunner({"browser": driver}, policy, audit, settings)


async def test_run_completes(policy, audit, settings):
    driver = FakeDriver()
    runner = build(driver, policy, audit, settings)
    result = await runner.run(recipe(make_step("a"), make_step("b")))
    assert result.status == "completed"
    assert [s.status for s in result.steps] == ["ok", "ok"]
    assert [c[0] for c in driver.calls] == ["ping", "ping"]


async def test_hitl_pauses_then_resume_executes(policy, audit, settings):
    driver = FakeDriver()
    runner = build(driver, policy, audit, settings)
    steps = [make_step("a"), make_step("danger", hitl=True), make_step("c")]
    result = await runner.run(recipe(*steps))
    assert result.status == "awaiting_human"
    assert result.pending_step == "danger"
    assert [c[0] for c in driver.calls] == ["ping"]

    resumed = await runner.resume(result.run_id, approve=True)
    assert resumed.status == "completed"
    assert [c[0] for c in driver.calls] == ["ping", "ping", "ping"]
    assert runner.load_state(result.run_id)["status"] == "completed"


async def test_failure_aborts(policy, audit, settings):
    driver = FakeDriver(fail_on={"boom"})
    runner = build(driver, policy, audit, settings)
    result = await runner.run(recipe(make_step("a"), make_step("x", action="boom"), make_step("c")))
    assert result.status == "failed"
    assert result.steps[-1].status == "failed"
    assert len(driver.calls) == 2


async def test_on_error_skip_continues(policy, audit, settings):
    driver = FakeDriver(fail_on={"boom"})
    runner = build(driver, policy, audit, settings)
    result = await runner.run(
        recipe(make_step("x", action="boom", on_error="skip"), make_step("c"))
    )
    assert result.status == "completed"
    assert [s.status for s in result.steps] == ["skipped", "ok"]


async def test_retries_attempt_count(policy, audit, settings):
    driver = FakeDriver(fail_on={"boom"})
    runner = build(driver, policy, audit, settings)
    result = await runner.run(recipe(make_step("x", action="boom", retries=2)))
    assert result.status == "failed"
    assert len(driver.calls) == 3


async def test_dry_run_skips_driver(policy, audit, settings):
    settings.dry_run = True
    driver = FakeDriver()
    runner = build(driver, policy, audit, settings)
    result = await runner.run(recipe(make_step("a")))
    assert result.status == "completed"
    assert driver.calls == []
    assert result.steps[0].output["dry_run"] is True


async def test_template_rendered_into_params(policy, audit, settings):
    driver = FakeDriver()
    runner = build(driver, policy, audit, settings)
    step = make_step("a", params={"url": "https://{{host}}/p"})
    await runner.run(recipe(step, vars_={"host": "example.com"}))
    assert driver.calls[0][1]["url"] == "https://example.com/p"


async def test_unknown_driver_fails(policy, audit, settings):
    runner = RecipeRunner({}, policy, audit, settings)
    result = await runner.run(recipe(make_step("a")))
    assert result.status == "failed"
    assert "未注册的驱动" in (result.steps[0].error or "")


async def test_policy_blocked_step_skipped(policy, audit, settings):
    driver = FakeDriver()
    runner = build(driver, policy, audit, settings)
    step = make_step("nav", action="navigate", params={"url": "https://evil.test"})
    result = await runner.run(recipe(step))
    assert result.status == "failed"
    assert result.steps[0].status == "skipped"


async def test_state_file_written(policy, audit, settings):
    driver = FakeDriver()
    runner = build(driver, policy, audit, settings)
    result = await runner.run(recipe(make_step("a")))
    assert (settings.runs_dir / f"{result.run_id}.json").exists()
    assert runner.list_runs()[0]["run_id"] == result.run_id
