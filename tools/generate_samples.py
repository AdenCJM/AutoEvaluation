"""
Sample Generator
=================
Generates output samples by calling the configured LLM with the SKILL.md
as a system instruction and each prompt as a user message.

Usage:
    python3 tools/generate_samples.py \\
        --skill-path SKILL.md \\
        --prompts-path prompts/prompts.json \\
        --output-dir .tmp/samples/baseline/

    # Or read defaults from config:
    python3 tools/generate_samples.py --output-dir .tmp/samples/baseline/
"""

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from model_client import ModelClient
from utils import PROJECT_ROOT, load_config, split_prompts


def _generate_one(client: ModelClient, skill_content: str, prompt_data: dict, index: int, out_dir: Path, sample_name: str, replicate: int = 0) -> dict:
    """Generate a single sample. Thread-safe (ModelClient uses thread-safe SDK clients)."""
    prompt_id = prompt_data["id"]
    genre = prompt_data["genre"]
    user_prompt = prompt_data["prompt"]
    start = time.time()

    try:
        text = client.generate(
            system_prompt=skill_content,
            user_prompt=user_prompt,
        )
        elapsed = time.time() - start
        sample_path = out_dir / f"{sample_name}.txt"
        sample_path.write_text(text, encoding="utf-8")
        return {
            "index": index,
            "prompt_id": prompt_id,
            "genre": genre,
            "replicate": replicate,
            "file": sample_path.name,
            "word_count": len(text.split()),
            "elapsed_seconds": round(elapsed, 2),
        }
    except Exception as e:
        return {
            "index": index,
            "prompt_id": prompt_id,
            "genre": genre,
            "replicate": replicate,
            "file": None,
            "error": str(e),
        }


def generate_samples(
    skill_path: str,
    prompts_path: str,
    output_dir: str,
    client: ModelClient = None,
    num_samples=None,
    max_concurrent: int = 1,
    replicates: int = 1,
    prompt_set: str = "all",
    holdout_fraction: float = 0.0,
) -> dict:
    """Generate samples and save to output directory.

    replicates > 1 generates N completions per prompt (named _r0.._rN-1) so
    the aggregator can estimate score variance. prompt_set selects "train",
    "holdout", or "all" prompts (split via utils.split_prompts).
    """
    if client is None:
        client = ModelClient.from_config(str(PROJECT_ROOT / "config.yaml"))

    skill_content = Path(skill_path).read_text(encoding="utf-8")
    prompts = json.loads(Path(prompts_path).read_text(encoding="utf-8"))
    if prompt_set in ("train", "holdout"):
        train, holdout = split_prompts(prompts, holdout_fraction)
        prompts = train if prompt_set == "train" else holdout
        if not prompts:
            print(f"Error: prompt set '{prompt_set}' is empty (holdout_fraction={holdout_fraction})", file=sys.stderr)
            sys.exit(1)
    if num_samples is not None:
        prompts = prompts[:num_samples]

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    metadata = {
        "provider": client.provider,
        "model": client.model,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "skill_path": skill_path,
        "prompt_set": prompt_set,
        "replicates": replicates,
        "samples": [],
    }

    # Build the job list: one entry per (prompt, replicate). Replicate suffix
    # only appears when replicates > 1, keeping legacy sample names stable.
    jobs = []
    for i, p in enumerate(prompts):
        for k in range(max(1, replicates)):
            name = f"sample_{i}_{p['id']}_r{k}" if replicates > 1 else f"sample_{i}_{p['id']}"
            jobs.append((p, i, name, k))

    if max_concurrent > 1 and len(jobs) > 1:
        # Parallel generation
        print(f"  Generating {len(jobs)} samples ({len(prompts)} prompts × {max(1, replicates)} replicates) with {max_concurrent} workers...")
        results = []
        with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
            futures = {
                executor.submit(_generate_one, client, skill_content, p, i, out_dir, name, k): name
                for (p, i, name, k) in jobs
            }
            done_count = 0
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                done_count += 1
                if result.get("file"):
                    print(f"  [{done_count}/{len(jobs)}] {result['file']} — {result['word_count']} words, {result['elapsed_seconds']:.1f}s")
                else:
                    print(f"  [{done_count}/{len(jobs)}] {result['prompt_id']} r{result['replicate']} — FAILED: {result.get('error', 'unknown')}")
        metadata["samples"] = sorted(results, key=lambda r: (r["index"], r.get("replicate", 0)))
    else:
        # Serial generation (original behaviour)
        for n, (prompt_data, i, name, k) in enumerate(jobs):
            prompt_id = prompt_data["id"]
            genre = prompt_data["genre"]
            print(f"  [{n+1}/{len(jobs)}] Generating: {name} ({genre})...", end=" ", flush=True)
            result = _generate_one(client, skill_content, prompt_data, i, out_dir, name, k)
            metadata["samples"].append(result)
            if result.get("file"):
                print(f"done ({result['word_count']} words, {result['elapsed_seconds']:.1f}s)")
            else:
                print(f"FAILED: {result.get('error', 'unknown')}")

    # Save metadata
    meta_path = out_dir / "metadata.json"
    meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    succeeded = sum(1 for s in metadata["samples"] if s.get("file"))
    print(f"\nMetadata saved to {meta_path}")
    print(f"Generated {succeeded} / {len(jobs)} samples")

    return metadata


def main():
    cfg = load_config()
    parser = argparse.ArgumentParser(description="Generate samples using configured LLM")
    parser.add_argument("--skill-path", default=cfg.get("skill_path", "SKILL.md"))
    parser.add_argument("--prompts-path", default=cfg.get("prompts_path", "prompts/prompts.json"))
    parser.add_argument("--output-dir", required=True, help="Directory to save samples")
    parser.add_argument("--num-samples", type=int, default=None, help="Limit number of samples")
    parser.add_argument("--max-concurrent", type=int, default=cfg.get("max_concurrent", 1))
    parser.add_argument("--replicates", type=int, default=cfg.get("replicates_per_prompt", 1),
                        help="Completions per prompt (default: config replicates_per_prompt)")
    parser.add_argument("--prompt-set", choices=["all", "train", "holdout"], default="all",
                        help="Which prompt split to generate for")
    args = parser.parse_args()

    client = ModelClient.from_config(str(PROJECT_ROOT / "config.yaml"))

    generate_samples(
        skill_path=args.skill_path,
        prompts_path=args.prompts_path,
        output_dir=args.output_dir,
        client=client,
        num_samples=args.num_samples,
        max_concurrent=args.max_concurrent,
        replicates=args.replicates,
        prompt_set=args.prompt_set,
        holdout_fraction=float(cfg.get("holdout_fraction", 0.3)),
    )


if __name__ == "__main__":
    main()
