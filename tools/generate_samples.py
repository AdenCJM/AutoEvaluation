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
from utils import PROJECT_ROOT, load_config


def _generate_one(client: ModelClient, skill_content: str, prompt_data: dict, index: int, out_dir: Path) -> dict:
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
        sample_path = out_dir / f"sample_{index}_{prompt_id}.txt"
        sample_path.write_text(text, encoding="utf-8")
        return {
            "index": index,
            "prompt_id": prompt_id,
            "genre": genre,
            "file": sample_path.name,
            "word_count": len(text.split()),
            "elapsed_seconds": round(elapsed, 2),
        }
    except Exception as e:
        return {
            "index": index,
            "prompt_id": prompt_id,
            "genre": genre,
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
) -> dict:
    """Generate samples and save to output directory."""
    if client is None:
        client = ModelClient.from_config(str(PROJECT_ROOT / "config.yaml"))

    skill_content = Path(skill_path).read_text(encoding="utf-8")
    prompts = json.loads(Path(prompts_path).read_text(encoding="utf-8"))
    if num_samples is not None:
        prompts = prompts[:num_samples]

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    metadata = {
        "provider": client.provider,
        "model": client.model,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "skill_path": skill_path,
        "samples": [],
    }

    if max_concurrent > 1 and len(prompts) > 1:
        # Parallel generation
        print(f"  Generating {len(prompts)} samples with {max_concurrent} workers...")
        results = []
        with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
            futures = {
                executor.submit(_generate_one, client, skill_content, p, i, out_dir): i
                for i, p in enumerate(prompts)
            }
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                if result.get("file"):
                    print(f"  [{result['index']+1}/{len(prompts)}] {result['prompt_id']} — {result['word_count']} words, {result['elapsed_seconds']:.1f}s")
                else:
                    print(f"  [{result['index']+1}/{len(prompts)}] {result['prompt_id']} — FAILED: {result.get('error', 'unknown')}")
        metadata["samples"] = sorted(results, key=lambda r: r["index"])
    else:
        # Serial generation (original behaviour)
        for i, prompt_data in enumerate(prompts):
            prompt_id = prompt_data["id"]
            genre = prompt_data["genre"]
            print(f"  [{i+1}/{len(prompts)}] Generating: {prompt_id} ({genre})...", end=" ", flush=True)
            result = _generate_one(client, skill_content, prompt_data, i, out_dir)
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
    print(f"Generated {succeeded} / {len(prompts)} samples")

    return metadata


def main():
    cfg = load_config()
    parser = argparse.ArgumentParser(description="Generate samples using configured LLM")
    parser.add_argument("--skill-path", default=cfg.get("skill_path", "SKILL.md"))
    parser.add_argument("--prompts-path", default=cfg.get("prompts_path", "prompts/prompts.json"))
    parser.add_argument("--output-dir", required=True, help="Directory to save samples")
    parser.add_argument("--num-samples", type=int, default=None, help="Limit number of samples")
    parser.add_argument("--max-concurrent", type=int, default=cfg.get("max_concurrent", 1))
    args = parser.parse_args()

    client = ModelClient.from_config(str(PROJECT_ROOT / "config.yaml"))

    generate_samples(
        skill_path=args.skill_path,
        prompts_path=args.prompts_path,
        output_dir=args.output_dir,
        client=client,
        num_samples=args.num_samples,
        max_concurrent=args.max_concurrent,
    )


if __name__ == "__main__":
    main()
