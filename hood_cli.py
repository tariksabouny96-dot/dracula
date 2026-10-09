"""
HOOD Command-Line Interface & System Entrypoint
Governed by Master System Specification Sections 1, 10, 16 & Build Instructions Section 23.
"""

import sys
import os
import argparse
from pathlib import Path
from packages.config import load_config
from packages.contracts import SystemState, RiskLevel, TaskNode
from packages.auth.vault import SecretVault
from services.policy.approval_service import ApprovalService
from services.policy.governance import RiskEvaluator
from services.audit.service import AuditService
from services.memory.service import MemoryService
from services.model_gateway.router import ModelRouter
from services.model_gateway.cost_controller import CostController
from services.tool_gateway.gateway import ToolGateway
from services.tool_gateway.tools import (
    FSReadFileTool,
    FSWriteFileTool,
    FSListDirTool,
    ShellExecTool,
    GitOpsTool,
    TestRunnerTool,
    CheckpointTool
)
from services.browser.browser_service import BrowserService
from services.browser.browser_tools import (
    BrowserNavigateTool,
    BrowserInspectDOMTool,
    BrowserClickTool,
    BrowserFillTool,
    BrowserScreenshotTool,
    BrowserUploadTool,
    BrowserSubmitConsequentialTool
)
from services.dev_executor.service import DevelopmentExecutor
from services.dev_executor.dev_tools import (
    DevInspectProjectTool,
    DevStartServerTool,
    DevStopServerTool,
    DevRunTestsTool,
    DevApplyCodeChangeTool,
    DevRollbackTool
)
from services.core.emergency_stop import EmergencyStopController
from services.core.hood_commander import HoodCommander
from services.x_control.x_executive import XExecutiveController


from services.voice.voice_router import VoiceRouter
from services.interaction.interaction_service import InteractionService
from services.x_control.x_session_manager import XSessionManager
from ui.server import JarvisServer, HoodServer


from services.events.event_bus import DistributedEventBus
from services.nodes.contracts import CryptographicNodeIdentity, NodeType, NodeCapability
from services.nodes.manager import NodeManager
from services.nodes.health import NodeHealthMonitor
from services.nodes.migration import NodeMigrationBundle


class HoodSystemRuntime:
    """Initializes and holds active instances of all Hood Core V0 services."""

    def __init__(self, config_path: str = "hood.config.yaml"):
        self.config = load_config(Path(config_path))
        self.vault = SecretVault(Path(self.config.security.secret_vault_file))
        self.audit_service = AuditService(Path(self.config.storage.sqlite_path))
        self.memory_service = MemoryService(Path(self.config.storage.sqlite_path))
        self.cost_controller = CostController(self.config.budgets)
        self.model_router = ModelRouter(self.config, self.cost_controller)
        self.approval_service = ApprovalService()
        self.browser_service = BrowserService(self.config)
        self.dev_executor = DevelopmentExecutor(
            workspace_root=Path.cwd(),
            config=self.config,
            browser_service=self.browser_service
        )
        self.voice_router = VoiceRouter(self.config)
        # Durable emergency-stop latch: an engaged stop survives a restart.
        from packages.security import StopLatch
        self.data_dir = Path(os.environ.get("HOOD_DATA_DIR") or (Path.home() / ".hood"))
        self.stop_latch = StopLatch(self.data_dir / "emergency_stop.json")
        self.tool_gateway = ToolGateway(self.config, self.approval_service, self.audit_service,
                                        stop_latch=self.stop_latch)

        # Desktop Control & Governance
        from services.desktop import DesktopService, FinancialAdvisor, OvernightExecutionManager
        self.desktop_service = DesktopService(
            config=self.config,
            tool_gateway=self.tool_gateway,
            approval_service=self.approval_service,
            audit_service=self.audit_service
        )
        self.financial_advisor = FinancialAdvisor(
            config=self.config,
            approval_service=self.approval_service,
            audit_service=self.audit_service
        )
        self.overnight_manager = OvernightExecutionManager(
            audit_service=self.audit_service
        )

        self._register_tools()

        # Multi-node & distributed event bus
        local_node_id = f"node-{self.config.node_role}"
        self.event_bus = DistributedEventBus(node_id=local_node_id)
        self.local_node_identity = CryptographicNodeIdentity(node_id=local_node_id)
        self.node_manager = NodeManager()
        # Self-register local node as TRUSTED
        from services.nodes.contracts import NodeDescriptor
        import platform
        self.node_manager.enroll_node(NodeDescriptor(
            node_id=local_node_id,
            node_type=NodeType.LOCAL_PRIMARY if self.config.node_role == "local-primary" else NodeType.LAPTOP_NODE,
            hostname_alias=platform.node(),
            public_key_hex=self.local_node_identity.public_key_hex,
            operating_system=platform.system(),
            architecture=platform.machine(),
            cpu_cores=os.cpu_count() or 4,
            ram_gb=16.0,
            disk_free_gb=100.0,
            capabilities=[
                NodeCapability.LOCAL_FILESYSTEM,
                NodeCapability.DEVELOPMENT_EXECUTOR,
                NodeCapability.BROWSER_AUTOMATION,
                NodeCapability.MEMORY_STORAGE,
                NodeCapability.VOICE_OUTPUT
            ]
        ))
        self.node_manager.trust_node(local_node_id, authorized_by="Zak")
        self.node_health = NodeHealthMonitor(
            node_manager=self.node_manager,
            event_bus=self.event_bus
        )
        self.node_migration = NodeMigrationBundle(workspace_root=Path.cwd())

        # Evolution Engine (Level 1/2/3 Architecture, Arena & Active Learning)
        from services.evolution import EvolutionEngine
        self.evolution_engine = EvolutionEngine(workspace_root=Path.cwd())

        self.emergency_stop = EmergencyStopController(
            self.tool_gateway,
            self.audit_service,
            self.browser_service,
            self.dev_executor,
            event_bus=self.event_bus,
            node_manager=self.node_manager,
            evolution_engine=self.evolution_engine,
            desktop_service=self.desktop_service
        )
        # Multi-agent engine: live providers via the router (budgets, pricing), sandboxed tools,
        # independent verification. Simulated output is refused here.
        from services.agents import AgentEngine
        self.agent_engine = AgentEngine(self.data_dir / "agents", router=self.model_router,
                                        stop_latch=self.stop_latch)
        self.emergency_stop.attach("agent_engine", self.agent_engine.halt_all)
        self.commander = HoodCommander(
            self.config,
            self.model_router,
            self.approval_service,
            self.audit_service,
            self.memory_service
        )
        self.interaction_service = InteractionService(
            commander=self.commander,
            approval_service=self.approval_service,
            memory_service=self.memory_service,
            voice_router=self.voice_router,
            config=self.config
        )
        self.x_controller = XExecutiveController(self.memory_service, self.audit_service)
        self.x_session_manager = XSessionManager(
            approval_service=self.approval_service,
            audit_service=self.audit_service,
            x_controller=self.x_controller
        )
        self.interaction_service.x_session_manager = self.x_session_manager

    def _register_tools(self):
        self.tool_gateway.register_tool(FSReadFileTool(self.tool_gateway))
        self.tool_gateway.register_tool(FSWriteFileTool(self.tool_gateway))
        self.tool_gateway.register_tool(FSListDirTool(self.tool_gateway))
        self.tool_gateway.register_tool(ShellExecTool(self.tool_gateway))
        self.tool_gateway.register_tool(GitOpsTool(self.tool_gateway))
        self.tool_gateway.register_tool(TestRunnerTool(self.tool_gateway))
        self.tool_gateway.register_tool(CheckpointTool(self.tool_gateway))
        # Register browser tools
        self.tool_gateway.register_tool(BrowserNavigateTool(self.tool_gateway, self.browser_service))
        self.tool_gateway.register_tool(BrowserInspectDOMTool(self.tool_gateway, self.browser_service))
        self.tool_gateway.register_tool(BrowserClickTool(self.tool_gateway, self.browser_service))
        self.tool_gateway.register_tool(BrowserFillTool(self.tool_gateway, self.browser_service))
        self.tool_gateway.register_tool(BrowserScreenshotTool(self.tool_gateway, self.browser_service))
        self.tool_gateway.register_tool(BrowserUploadTool(self.tool_gateway, self.browser_service))
        self.tool_gateway.register_tool(BrowserSubmitConsequentialTool(self.tool_gateway, self.browser_service))
        # Register dev executor tools
        self.tool_gateway.register_tool(DevInspectProjectTool(self.tool_gateway, self.dev_executor))
        self.tool_gateway.register_tool(DevStartServerTool(self.tool_gateway, self.dev_executor))
        self.tool_gateway.register_tool(DevStopServerTool(self.tool_gateway, self.dev_executor))
        self.tool_gateway.register_tool(DevRunTestsTool(self.tool_gateway, self.dev_executor))
        self.tool_gateway.register_tool(DevApplyCodeChangeTool(self.tool_gateway, self.dev_executor))
        self.tool_gateway.register_tool(DevRollbackTool(self.tool_gateway, self.dev_executor))
        # Register desktop tools
        from services.tool_gateway.tools import DesktopControlTool
        self.tool_gateway.register_tool(DesktopControlTool(self.tool_gateway, self.desktop_service))

    def health_check(self) -> dict:
        gemini_ok = self.model_router.providers.get("gemini").is_healthy()
        mock_ok = self.model_router.providers.get("mock").is_healthy()
        openai_enabled = self.config.providers.get("openai").enabled

        return {
            "status": "HEALTHY",
            "environment": self.config.environment.value,
            "node_role": self.config.node_role,
            "emergency_stop_active": self.emergency_stop.is_active,
            "providers": {
                "gemini": "ONLINE" if gemini_ok else "CONFIGURED_PENDING_KEY",
                "mock": "ONLINE" if mock_ok else "OFFLINE",
                "openai": "ENABLED" if openai_enabled else "DISABLED_PENDING_AUTH"
            },
            "vault_backend": self.config.security.secret_vault_backend,
            "vault_secret_count": len(self.vault.list_references()),
            "budgets": self.cost_controller.get_summary()
        }


def _agent_command(runtime, args):
    """CLI front-end for the agent engine. The local operator acts as the Root Owner."""
    import json as _json
    engine, owner = runtime.agent_engine, "user_root_owner_01"
    cmd = args.agent_command
    if cmd == "create":
        result = engine.create_mission(owner, args.objective, args.budget)
    elif cmd == "approve":
        result = engine.approve_plan(owner, args.id, args.plan_sha256, approver="local-cli-operator")
    elif cmd == "run":
        result = engine.run(owner, args.id)
    elif cmd == "status":
        result = engine.status(owner, args.id)
    elif cmd == "cancel":
        result = engine.cancel(owner, args.id, actor="local-cli-operator")
    elif cmd == "events":
        result = engine.events(owner, args.id)
    elif cmd == "list":
        result = engine.list(owner)
    elif cmd == "artifact":
        name, data, digest = engine.artifact(owner, args.id)
        with open(args.out, "xb") as handle:
            handle.write(data)
        result = {"written": args.out, "name": name, "sha256": digest, "bytes": len(data)}
    else:
        print("Usage: hood_cli.py agent {create,approve,run,status,cancel,events,list,artifact}")
        sys.exit(2)
    print(_json.dumps(result, indent=2, default=str))


def main():
    parser = argparse.ArgumentParser(description="HOOD Personal AI Operating System")
    subparsers = parser.add_subparsers(dest="command")

    # Status
    subparsers.add_parser("status", help="Display system status and health check")

    # Plan
    plan_parser = subparsers.add_parser("plan", help="Decompose an objective and plan execution")
    plan_parser.add_argument("objective", help="High-level goal or instruction")

    # Solutions
    sol_parser = subparsers.add_parser("solutions", help="Formulate 3 viable solutions and recommendation")
    sol_parser.add_argument("problem", help="Problem description")

    # Emergency Stop
    subparsers.add_parser("stop", help="Trigger Emergency Stop ('Hood, stop everything')")
    reset_parser = subparsers.add_parser("stop-reset", help="Release the emergency stop (local Root Owner operator)")
    reset_parser.add_argument("--confirm", action="store_true", help="Required: confirm the release")

    agent_parser = subparsers.add_parser("agent", help="Multi-agent missions: plan, approve, run, inspect, download")
    agent_sub = agent_parser.add_subparsers(dest="agent_command")
    a_create = agent_sub.add_parser("create", help="Plan a mission (calls the configured model provider)")
    a_create.add_argument("--objective", required=True)
    a_create.add_argument("--budget", type=float, default=1.0, help="Mission spend cap in USD")
    a_approve = agent_sub.add_parser("approve", help="Approve the exact plan hash shown by 'create'/'status'")
    a_approve.add_argument("--id", required=True)
    a_approve.add_argument("--plan-sha256", required=True)
    for name in ("run", "status", "cancel", "events"):
        agent_sub.add_parser(name).add_argument("--id", required=True)
    a_art = agent_sub.add_parser("artifact", help="Write the verified artifact zip")
    a_art.add_argument("--id", required=True)
    a_art.add_argument("--out", required=True)
    agent_sub.add_parser("list")

    # Multi-agent repository audit
    subparsers.add_parser("audit", help="Run multi-agent repository inspection and audit task")

    # Development commands
    inspect_parser = subparsers.add_parser("dev-inspect", help="Inspect project structure, stack, and framework")
    inspect_parser.add_argument("path", nargs="?", default=".", help="Project path to inspect")

    test_parser = subparsers.add_parser("dev-test", help="Run test suite and classify failures")
    test_parser.add_argument("path", nargs="?", default="tests", help="Test path to execute")

    subparsers.add_parser("dev-fix", help="Run autonomous break/fix repair run on demo_app")

    # HOOD Interactive Surface commands
    ui_parser = subparsers.add_parser("ui", help="Launch HOOD interactive surface")
    ui_parser.add_argument("--port", type=int, default=8990, help="Port to bind HOOD Interactive Surface GUI")

    # Multi-Node Commands
    subparsers.add_parser("node-list", help="List all enrolled and trusted cluster nodes")

    enroll_parser = subparsers.add_parser("node-enroll", help="Enroll a new remote node in the cluster")
    enroll_parser.add_argument("--node-id", required=True, help="Unique node identifier")
    enroll_parser.add_argument("--name", required=True, help="Friendly name of the node")
    enroll_parser.add_argument("--node-type", default="laptop_node", help="Node type (cloud_core, laptop_node, gpu_worker)")
    enroll_parser.add_argument("--endpoint", default="http://127.0.0.1:8995", help="Node HTTP transport endpoint")
    enroll_parser.add_argument("--pubkey", required=True, help="Base64 public key for Ed25519 authentication")

    export_parser = subparsers.add_parser("export-node", help="Export node state and memory into a verified bundle")
    export_parser.add_argument("--output", default="artifacts/node_backup.tar.gz", help="Path to output bundle")

    import_parser = subparsers.add_parser("import-node", help="Import node state from a verified migration bundle")
    import_parser.add_argument("--bundle", required=True, help="Path to input bundle")

    # Evolution Engine Commands
    subparsers.add_parser("evolution-status", help="Display Evolution Engine 3-level model architecture and promotion status")
    subparsers.add_parser("evolution-models", help="List all registered models across Level 1, Level 2, and Level 3")
    subparsers.add_parser("evolution-hardware", help="Inspect detected and registered AI hardware topologies")

    breakeven_parser = subparsers.add_parser("evolution-breakeven", help="Compute GPU break-even financial analysis vs API and cloud rental")
    breakeven_parser.add_argument("--price", type=float, default=1600.0, help="Purchase price in USD (e.g. RTX 4090)")
    breakeven_parser.add_argument("--hours", type=float, default=150.0, help="Monthly utilization in hours")
    breakeven_parser.add_argument("--rental-rate", type=float, default=0.79, help="Equivalent rental rate per hour in USD")
    # Desktop & Governance Commands
    subparsers.add_parser("desktop-status", help="Inspect Desktop Control engine state and active window")
    subparsers.add_parser("desktop-windows", help="Enumerate visible desktop application windows")
    subparsers.add_parser("desktop-observe", help="Perform governed desktop screen observation and secret scrubbing")
    subparsers.add_parser("unattended-status", help="Inspect unattended / overnight execution branches and morning report")
    subparsers.add_parser("handoff-status", help="Inspect pending human verification challenges (CAPTCHA, MFA, OTP)")
    subparsers.add_parser("finance-pending", help="List pending financial expenditure recommendations")
    subparsers.add_parser("finance-history", help="Audit history of financial decisions")

    args = parser.parse_args()
    runtime = HoodSystemRuntime()

    if args.command == "status" or not args.command:
        health = runtime.health_check()
        print("========================================")
        print(" HOOD CORE V0 - STATUS REPORT")
        print("========================================")
        print(f"Status:        {health['status']}")
        print(f"Environment:   {health['environment']}")
        print(f"Node Role:     {health['node_role']}")
        print(f"Emergency Stop: {'ACTIVE' if health['emergency_stop_active'] else 'INACTIVE'}")
        print("Providers:")
        for p, s in health["providers"].items():
            print(f"  - {p}: {s}")
        print(f"Vault backend: {health['vault_backend']} ({health['vault_secret_count']} secrets referenced)")
        print(f"Daily Spend:   ${health['budgets']['daily_spend_usd']} / ${health['budgets']['max_daily_limit_usd']}")
        print("========================================")

    elif args.command == "dev-inspect":
        info = runtime.dev_executor.inspect_project(args.path)
        print(f"Project: {info.project_root}")
        print(f"Language: {info.primary_language}")
        print(f"Framework: {info.framework or 'None detected'}")
        print(f"Package Manager: {info.package_manager or 'None detected'}")
        print(f"Test Framework: {info.test_framework or 'None detected'}")
        print(f"Entrypoints: {info.entrypoints}")

    elif args.command == "dev-test":
        report = runtime.dev_executor.run_tests(test_path=args.path)
        print(f"Tests: {'SUCCESS' if report.success else 'FAILED'} (Exit code: {report.exit_code})")
        print(f"Passed: {report.total_passed}, Failed: {report.total_failed}, Errors: {report.total_errors}")
        if report.failures:
            print(f"Failures detected ({len(report.failures)}):")
            for f in report.failures:
                print(f"  - [{f.failure_type}] {f.test_id}: {f.message}")

    elif args.command == "dev-fix":
        print("Executing Autonomous Break/Fix Lifecycle on demo_app...")
        defective_chunk = "    if tier == 0:\n        # Deliberate bug for reproduction:\n        return price / tier\n    return (price * tier) / 100.0"
        fixed_chunk = "    if tier == 0:\n        return 0.0\n    return (price * tier) / 100.0"
        res = runtime.commander.execute_autonomous_development_task(
            project_subpath="demo_app",
            test_file="demo_app/tests/test_app.py",
            source_file="demo_app/app_service.py",
            original_chunk=defective_chunk,
            fixed_chunk=fixed_chunk,
            dev_executor=runtime.dev_executor
        )
        print(f"Status: {res['status']}")
        print(f"Fix Verified: {res['fix_verified']}")
        print(f"Maker-Checker: {res['checker_verification']['validator']} -> {res['checker_verification']['claim']}")
        # Leave demo app restored to fixture state
        if res.get("edit_result") and res["edit_result"].get("checkpoint_ref"):
            runtime.dev_executor.rollback_code_change("demo_app/app_service.py", res["edit_result"]["checkpoint_ref"])
            print("Checkpoint rolled back to preserve clean baseline fixture.")

    elif args.command == "ui":
        print(f"Launching HOOD Interactive Surface on http://127.0.0.1:{args.port} ...")
        from services.auth.auth_service import AuthenticationService
        auth_svc = getattr(runtime, "auth_service", None) or AuthenticationService()
        server = JarvisServer(
            interaction_service=runtime.interaction_service,
            emergency_stop=runtime.emergency_stop,
            runtime=runtime,
            port=args.port,
            auth_service=auth_svc
        )
        print("HOOD Interface running. Press Ctrl+C to halt.")
        try:
            server.httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down HOOD UI server.")
            server.stop()

    elif args.command == "voice":
        print(f"Zak (Voice): {args.phrase}")
        resp = runtime.interaction_service.handle_voice_input(b"simulated_pcm_audio")
        print(f"Hood (Voice): {resp.text}")
        print(f"Modality: {resp.modality} | Interrupted: {resp.interrupted}")

    elif args.command == "plan":
        tasks = runtime.commander.plan_objective(args.objective)
        print("================================================================================")
        print("  HOOD GOVERNED PLAN EXECUTION")
        print("================================================================================")
        print(f"Objective: {args.objective}\n")
        print("Planned Task Graph:")
        for t in tasks:
            print(f"  [Task] {t.title} -> Assigned: {t.assigned_agent} (Risk: {t.risk_level.value})")
        print("\nExecuting Tasks...")
        results = runtime.commander.execute_plan(tasks)
        synthesis = runtime.commander.synthesize_objective_result(results)
        print("\n================================================================================")
        print(synthesis["formatted_text"])
        print("================================================================================")

    elif args.command == "solutions":
        res = runtime.commander.formulate_three_solutions(args.problem)
        print(f"Problem: {res['problem']}\n")
        print("Three Viable Options (H12/H13):")
        for opt in res["three_options"]:
            print(f"  * {opt['name']}: {opt['pros']} (Risk: {opt['risk']}, Confidence: {opt['confidence']})")
        print(f"\nHood Recommendation (H14): {res['hood_recommendation']}")

    elif args.command == "stop":
        res = runtime.emergency_stop.trigger_stop("CLI emergency stop")
        print(f"Emergency Stop: {res['status']} (latch engaged: {res['latch_engaged']})")
        for name, outcome in res["subsystems"].items():
            print(f"  {name:14} {outcome['status']}" + (f"  {outcome.get('error')}" if outcome.get("error") else ""))
        if res["incomplete"]:
            print("  NOT CONFIRMED HALTED: " + ", ".join(res["incomplete"]))

    elif args.command == "stop-reset":
        if not args.confirm:
            print("Refusing: pass --confirm to release the emergency stop.")
            sys.exit(2)
        # The CLI runs as the local OS account that owns Hood's data files (Root Owner equivalent).
        runtime.emergency_stop.reset_stop(authorized_by="local-cli-operator", is_root_owner=True)
        print("Emergency stop released.")

    elif args.command == "agent":
        _agent_command(runtime, args)

    elif args.command == "audit":
        print("Running HOOD Multi-Agent Repository Audit Task...")
        audit_res = runtime.commander.execute_repository_audit_task(runtime.tool_gateway)
        print("Audit Task Complete:")
        for t in audit_res["tasks"]:
            res_node = audit_res["task_results"][t.task_id]
            print(f"  - [{res_node.assigned_agent}] {res_node.title}: {res_node.status.value}")
        synth = audit_res["synthesis"]
        print(f"\nChecker Validation: {synth['checker_verification']}")
        print(f"Recommendation: {synth['consequential_recommendation']}")

    elif args.command == "node-list":
        nodes = runtime.node_manager.list_nodes()
        print("========================================")
        print(" HOOD MULTI-NODE CLUSTER TOPOLOGY")
        print("========================================")
        for n in nodes:
            caps = ", ".join([c for c in n.get("capabilities", [])])
            print(f"Node: {n.get('hostname_alias')} [{n.get('node_id')}]")
            print(f"  Type:        {n.get('node_type')}")
            print(f"  Trust State: {n.get('trust_state')}")
            print(f"  Status:      {'ONLINE' if n.get('online_status') else 'OFFLINE'}")
            print(f"  OS/Arch:     {n.get('operating_system')} / {n.get('architecture')}")
            print(f"  Capabilities: {caps}")
            print("----------------------------------------")

    elif args.command == "node-enroll":
        from services.nodes.contracts import NodeDescriptor, NodeType, NodeCapability
        node_type_map = {
            "cloud_core": NodeType.CLOUD_CORE,
            "laptop_node": NodeType.LAPTOP_NODE,
            "gpu_worker": NodeType.GPU_WORKER
        }
        descriptor = NodeDescriptor(
            node_id=args.node_id,
            node_type=node_type_map.get(args.node_type, NodeType.LAPTOP_NODE),
            hostname_alias=args.name,
            public_key_hex=args.pubkey,
            operating_system="Unknown",
            architecture="Unknown",
            cpu_cores=4,
            ram_gb=16.0,
            disk_free_gb=50.0,
            capabilities=[NodeCapability.LOCAL_FILESYSTEM, NodeCapability.DEVELOPMENT_EXECUTOR]
        )
        runtime.node_manager.enroll_node(descriptor)
        print(f"Node '{args.name}' ({args.node_id}) enrolled successfully in ENROLLED status.")
        print("Note: Requires explicit trust validation by Zak before receiving tasks.")

    elif args.command == "export-node":
        out_path = Path(args.output)
        res = runtime.node_migration.export_node(
            output_tar_path=out_path,
            node_id=runtime.local_node_identity.node_id,
            sqlite_db_path=Path(runtime.config.storage.sqlite_path)
        )
        print(f"Export Bundle Created: {res['bundle_path']}")
        print(f"Records Exported:     {res['records_exported']}")
        print(f"Archive SHA256:       {res['archive_sha256']}")

    elif args.command == "import-node":
        bundle_path = Path(args.bundle)
        res = runtime.node_migration.import_node(
            tar_path=bundle_path,
            target_sqlite_path=Path(runtime.config.storage.sqlite_path)
        )
        print(f"Import Complete for Node: {res['node_id']}")
        print(f"Imported Records:        {res['imported_records']}")
        print(f"Integrity Verified:      {res['integrity_verified']}")

    elif args.command == "evolution-status":
        print("========================================")
        print(" HOOD EVOLUTION ENGINE - 3-LEVEL STATUS")
        print("========================================")
        from services.evolution.contracts import ModelLevel
        for lvl in [ModelLevel.LEVEL_1_EXTERNAL, ModelLevel.LEVEL_2_SELF_HOSTED, ModelLevel.LEVEL_3_HOOD]:
            models = runtime.evolution_engine.registry.list_models_by_level(lvl)
            print(f"\n{lvl.value}:")
            for m in models:
                caps = ", ".join([c.value for c in m.capabilities])
                print(f"  * {m.model_id} (v{m.version})")
                print(f"    State:        {m.promotion_state.value}")
                print(f"    Runtime:      {m.provider_runtime}")
                print(f"    Capabilities: {caps}")
                print(f"    Privacy:      {'LOCAL_COMPLIANT' if m.is_privacy_compliant else 'EXTERNAL_API'}")
        print("========================================")

    elif args.command == "evolution-models":
        print("========================================")
        print(" REGISTERED MODELS & PROMOTION STATES")
        print("========================================")
        for m in runtime.evolution_engine.registry._models.values():
            print(f"Model ID:        {m.model_id}")
            print(f"  Level:         {m.level.value}")
            print(f"  Promotion:     {m.promotion_state.value}")
            print(f"  Base Model:    {m.base_model or 'None'}")
            print(f"  Cost (in/out): ${m.cost_per_1k_input} / ${m.cost_per_1k_output}")
            print("----------------------------------------")

    elif args.command == "evolution-hardware":
        print("========================================")
        print(" AI HARDWARE TOPOLOGIES & VRAM SPECS")
        print("========================================")
        for name, hw in runtime.evolution_engine.hardware_mgr.profiles.items():
            print(f"Topology: {name} [{hw.ownership.value}]")
            print(f"  Vendor/Model:    {hw.gpu_vendor} {hw.gpu_model}")
            print(f"  GPU Count:       {hw.gpu_count}")
            print(f"  Per-Device VRAM: {hw.per_device_vram_gb} GB")
            print(f"  Aggregate VRAM:  {hw.aggregate_vram_gb} GB")
            print(f"  Topology Note:   Single-device pooling across devices is strictly disallowed.")
            print("----------------------------------------")

    elif args.command == "evolution-breakeven":
        from services.evolution.hardware import GPUBreakEvenCalculator
        be = GPUBreakEvenCalculator.calculate_break_even(
            hardware_name="NVIDIA RTX 4090 (Simulated)",
            purchase_price_usd=args.price,
            depreciation_period_months=24,
            average_power_draw_watts=350.0,
            electricity_cost_kwh_usd=0.15,
            monthly_utilization_hours=args.hours,
            equivalent_rental_rate_hour_usd=args.rental_rate,
            equivalent_api_cost_monthly_usd=args.api_spend
        )
        print("========================================")
        print(" GPU BREAK-EVEN FINANCIAL ACCOUNTING")
        print("========================================")
        print(f"Hardware:               {be.hardware_name}")
        print(f"Purchase Price:         ${be.purchase_price_usd}")
        print(f"Depreciation (24 mo):   ${be.monthly_depreciation_usd} / mo")
        print(f"Monthly Power:          ${be.monthly_electricity_cost_usd} ({args.hours} hrs @ 350W)")
        print(f"Total Owned Run Cost:   ${be.monthly_owned_cost_total_usd} / mo")
        print("----------------------------------------")
        print(f"Cloud Rental (Equiv):   ${be.monthly_rented_equivalent_cost_usd} / mo (@ ${args.rental_rate}/hr)")
        print(f"API Spend (Equiv):      ${be.equivalent_api_cost_monthly_usd} / mo")
        print("----------------------------------------")
        print(f"Break-Even vs Rental:   {be.break_even_vs_rental_months} months")
        print(f"Break-Even vs API:      {be.break_even_vs_api_months} months")
        print(f"Purchase Recommended:   {be.purchase_recommended}")
        print(f"Analysis Rationale:     {be.rationale}")
        print("========================================")

    elif args.command == "desktop-status":
        active_w = runtime.desktop_service.inspect_active_window()
        w, h = runtime.desktop_service.backend.get_screen_dimensions()
        print("========================================")
        print(" HOOD GOVERNED DESKTOP CONTROL STATUS")
        print("========================================")
        print(f"Status:            {'EMERGENCY_STOP' if runtime.desktop_service.is_emergency_stopped else 'ONLINE'}")
        print(f"Display Geometry:  {w} x {h}")
        print(f"Active Window:     {active_w.title if active_w else 'None'}")
        print(f"Active Process:    {active_w.process_name if active_w else 'None'} (PID: {active_w.process_id if active_w else 0})")
        print(f"Control Hierarchy: API -> HTTP -> CLI -> Playwright -> UI Automation -> Vision -> Mouse")
        print("========================================")

    elif args.command == "desktop-windows":
        windows = runtime.desktop_service.enumerate_windows()
        print("========================================")
        print(f" VISIBLE DESKTOP WINDOWS ({len(windows)} found)")
        print("========================================")
        for w in windows:
            active_mark = " [*ACTIVE*]" if w.is_active else ""
            print(f"HWND {w.hwnd:8d} | PID {w.process_id:6d} ({w.process_name:18s}) | {w.title[:45]}{active_mark}")
        print("========================================")

    elif args.command == "desktop-observe":
        obs = runtime.desktop_service.observe_screen(capture_image=True)
        print("========================================")
        print(" DESKTOP SCREEN OBSERVATION")
        print("========================================")
        print(f"Observation ID:    {obs.observation_id}")
        print(f"Geometry:          {obs.screen_width} x {obs.screen_height}")
        print(f"Active Window:     {obs.active_window.title if obs.active_window else 'None'}")
        print(f"Discovered UI:     {len(obs.discovered_elements)} semantic elements")
        print(f"Screenshot Path:   {obs.screenshot_path}")
        print(f"Image SHA256:      {obs.image_hash_sha256[:16]}...")
        print(f"Secrets Scrubbed:  {obs.has_redacted_secrets} ({obs.redaction_count} redacted)")
        print("========================================")

    elif args.command == "unattended-status":
        report = runtime.overnight_manager.generate_morning_report()
        print("========================================")
        print(" UNATTENDED / OVERNIGHT EXECUTION STATUS")
        print("========================================")
        print(f"Period:            {report.period_start.strftime('%Y-%m-%d %H:%M:%S')} -> {report.period_end.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Completed Tasks:   {len(report.completed_tasks)}")
        print(f"Failed Tasks:      {len(report.failed_tasks)}")
        print(f"Waiting Approval:  {len(report.waiting_for_approval)}")
        print(f"Waiting Human:     {len(report.waiting_for_human_verification)}")
        print(f"Cost Incurred:     ${report.cost_incurred_usd:.2f}")
        print(f"Free Fallbacks:    {len(report.free_fallbacks_used)}")
        print("========================================")

    elif args.command == "handoff-status":
        pending = runtime.desktop_service.handoff_mgr.list_pending_challenges()
        print("========================================")
        print(f" HUMAN VERIFICATION CHALLENGES ({len(pending)} pending)")
        print("========================================")
        if not pending:
            print("No verification barriers currently active.")
        for p in pending:
            print(f"Challenge [{p.challenge_type.value}] - Task: {p.task_id}")
            print(f"  Description: {p.description}")
            print(f"  Status:      {p.status.value}")
        print("========================================")

    elif args.command == "finance-pending":
        recs = [r for r in runtime.financial_advisor.recommendations.values() if r.approval_status.value == "PENDING"]
        print("========================================")
        print(f" PENDING FINANCIAL RECOMMENDATIONS ({len(recs)})")
        print("========================================")
        if not recs:
            print("No pending financial expenditure requests. Budget: $0.00 spend.")
        for r in recs:
            print(f"Recommendation: {r.recommendation_id}")
            print(f"  Need:         {r.need_description}")
            print(f"  Recommended:  {r.recommended_option} (${r.expected_cost_usd:.2f})")
            print(f"  Free Option:  {r.free_alternative.option_name} ($0.00)")
            print(f"  Consequence:  {r.consequence_of_free}")
        print("========================================")

    elif args.command == "finance-history":
        events = runtime.audit_service.query_events(action="FINANCIAL")
        print("========================================")
        print(f" FINANCIAL DECISION AUDIT TRAIL ({len(events)} records)")
        print("========================================")
        for e in events:
            print(f"[{e.timestamp.strftime('%H:%M:%S')}] {e.actor} -> {e.action} | Result: {e.result[:60]}")
        print("========================================")


if __name__ == "__main__":
    main()

