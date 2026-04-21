from __future__ import annotations

import argparse
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from polyplace_contracts.deploy import Deployment


@dataclass
class ForgeDeployment(Deployment):
    chain_id: int
    deployer: str
    initial_supply: int
    claim_amount: int
    cooldown: int
    rent_price: int
    rent_duration: int


def contracts_repo_root() -> Path:
    override = os.environ.get("POLYPLACE_CONTRACTS_REPO")
    if override:
        root = Path(override).expanduser().resolve()
    else:
        root = Path(__file__).resolve().parents[2] / "polyplace-contracts"

    if not root.is_dir():
        raise RuntimeError(
            "Cannot find sibling polyplace-contracts repo. "
            "Set POLYPLACE_CONTRACTS_REPO to the repo root if it lives elsewhere."
        )

    return root


def load_forge_deployment(path: str | Path) -> ForgeDeployment:
    data = json.loads(Path(path).read_text())
    return ForgeDeployment(
        token=data["token"],
        faucet=data["faucet"],
        grid=data["grid"],
        chain_id=int(data["chainId"]),
        deployer=data["deployer"],
        initial_supply=int(data["initialSupply"]),
        claim_amount=int(data["claimAmount"]),
        cooldown=int(data["cooldown"]),
        rent_price=int(data["rentPrice"]),
        rent_duration=int(data["rentDuration"]),
    )


def deploy_via_forge(
    *,
    rpc_url: str,
    private_key: str,
    manifest_path: str | Path,
    claim_amount: int | None = None,
    cooldown: int | None = None,
    rent_price: int | None = None,
    rent_duration: int | None = None,
    repo_root: str | Path | None = None,
) -> ForgeDeployment:
    contracts_root = Path(repo_root).resolve() if repo_root is not None else contracts_repo_root()
    output_path = Path(manifest_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    forge_output_dir = contracts_root / ".forge-manifests"
    forge_output_dir.mkdir(parents=True, exist_ok=True)
    forge_output_path = forge_output_dir / f"{output_path.stem}-{uuid4().hex}.json"

    env = os.environ.copy()
    env["PRIVATE_KEY"] = private_key
    env["POLYPLACE_DEPLOYMENT_MANIFEST_PATH"] = str(forge_output_path)
    if claim_amount is not None:
        env["POLYPLACE_DEPLOY_CLAIM_AMOUNT"] = str(claim_amount)
    if cooldown is not None:
        env["POLYPLACE_DEPLOY_COOLDOWN"] = str(cooldown)
    if rent_price is not None:
        env["POLYPLACE_DEPLOY_RENT_PRICE"] = str(rent_price)
    if rent_duration is not None:
        env["POLYPLACE_DEPLOY_RENT_DURATION"] = str(rent_duration)

    cmd = [
        "forge",
        "script",
        "script/Deploy.s.sol",
        "--rpc-url",
        rpc_url,
        "--broadcast",
        "--slow",
    ]
    proc = subprocess.run(
        cmd,
        cwd=contracts_root,
        env=env,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "Forge deploy failed.\n"
            f"cwd: {contracts_root}\n"
            f"command: {' '.join(cmd)}\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}"
        )

    if not forge_output_path.is_file():
        raise RuntimeError(f"Forge deploy did not write manifest: {forge_output_path}")

    output_path.write_text(forge_output_path.read_text())

    return load_forge_deployment(output_path)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deploy Polyplace contracts with Forge and print the manifest JSON.")
    parser.add_argument("--rpc-url", required=True)
    parser.add_argument("--private-key", required=True)
    parser.add_argument("--manifest-path", required=True)
    parser.add_argument("--contracts-repo")
    parser.add_argument("--claim-amount", type=int)
    parser.add_argument("--cooldown", type=int)
    parser.add_argument("--rent-price", type=int)
    parser.add_argument("--rent-duration", type=int)
    return parser.parse_args()


def _json_for_stdout(deployment: ForgeDeployment) -> dict[str, Any]:
    return {
        "token": deployment.token,
        "faucet": deployment.faucet,
        "grid": deployment.grid,
        "chainId": deployment.chain_id,
        "deployer": deployment.deployer,
        "initialSupply": str(deployment.initial_supply),
        "claimAmount": str(deployment.claim_amount),
        "cooldown": deployment.cooldown,
        "rentPrice": str(deployment.rent_price),
        "rentDuration": deployment.rent_duration,
    }


def main() -> None:
    args = _parse_args()
    deployment = deploy_via_forge(
        rpc_url=args.rpc_url,
        private_key=args.private_key,
        manifest_path=args.manifest_path,
        claim_amount=args.claim_amount,
        cooldown=args.cooldown,
        rent_price=args.rent_price,
        rent_duration=args.rent_duration,
        repo_root=args.contracts_repo,
    )
    print(json.dumps(_json_for_stdout(deployment)))


if __name__ == "__main__":
    main()
