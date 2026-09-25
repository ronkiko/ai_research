"""Frozen learned-policy acceptance through an already running Host."""
import argparse
import json
from pathlib import Path
import random

import torch

from organism.host import HostClient, HostError
from organism.models import model_for_checkpoint, policy_id
from organism.training import collect_episode, verify_spine_policy, verify_recovery_policy


def main():
    torch.set_num_threads(1)
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--random-goals", action="store_true",
                        help="also verify 40 fixed-seed held-out goals")
    args = parser.parse_args()
    model, _ = model_for_checkpoint(args.checkpoint)
    client = HostClient("gamelab-existing-frozen-test")
    owned_player = None
    try:
        try:
            player = client.session()["player_id"]
        except HostError as exc:
            if "not logged in" not in str(exc):
                raise
            client.login("player1")
            owned_player = "player1"
            player = owned_player
        result = verify_spine_policy(model, client, player_id=player)
        result["policy_id"] = policy_id(model)
        result["recovery"] = verify_recovery_policy(model, client, player_id=player)
        result["passed"] = result["passed"] and result["recovery"]["passed"]
        if args.random_goals:
            rng = random.Random(983)
            cases = []
            for index in range(40):
                spawn = rng.uniform(20., 980.)
                target = (rng.uniform(20., 980.) if index % 2 else
                          max(20., min(980., spawn + rng.uniform(-40., 40.))))
                trial = collect_episode(model, client, player_id=player, spawn_x=spawn,
                                        target_x=target, max_seconds=8., sampled=False)
                cases.append({"spawn_x": spawn, "target_x": target,
                              "passed": trial.result == "success" and trial.evidence["wall_contacts"] == 0,
                              "error": trial.final_error, "vx": trial.evidence["vx"],
                              "result": trial.result})
            result["heldout"] = {"passed": all(case["passed"] for case in cases),
                                 "cases": cases}
            result["passed"] = result["passed"] and result["heldout"]["passed"]
        print(json.dumps(result, sort_keys=True), flush=True)
        return 0 if result["passed"] else 2
    finally:
        if owned_player is not None:
            try:
                if client.session().get("player_id") == owned_player:
                    client.logout()
            except HostError:
                pass
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
