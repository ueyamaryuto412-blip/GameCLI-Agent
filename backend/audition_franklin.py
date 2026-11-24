"""Generate offline voice-clone samples from the approved Franklin reference."""
import argparse
import json
from pathlib import Path
from time import perf_counter


REFERENCE = Path(__file__).resolve().parents[1] / "studio" / "voices" / "franklin"
MODEL = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--output-dir", type=Path, required=True, help="New directory for WAVs and timings")
    cli.add_argument("--model", default=MODEL, help="Hugging Face ID or a local Base model directory")
    cli.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    cli.add_argument("--text", action="append", help="Japanese line to speak (repeatable)")
    args = cli.parse_args()
    if args.output_dir.exists():
        cli.error("--output-dir must be a new directory")

    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel

    if args.device == "cuda" and not torch.cuda.is_available():
        cli.error("CUDA unavailable. Check the PyTorch install or use --device cpu.")
    if args.device == "cpu":
        torch.set_num_threads(6)
    started = perf_counter()
    model = Qwen3TTSModel.from_pretrained(
        args.model, device_map=args.device, dtype=torch.bfloat16, attn_implementation="sdpa",
    )
    reference = json.loads((REFERENCE / "reference.json").read_text(encoding="utf-8"))
    samples, rate = sf.read(REFERENCE / "reference.wav", dtype="float32")
    prompt = model.create_voice_clone_prompt(
        ref_audio=(samples, rate), ref_text=reference["reference_text"], x_vector_only_mode=False,
    )
    lines = args.text or ["おや、ビッグバンから剣が出た。"]
    report = {"model": str(args.model), "device": args.device, "reference_sha256": reference["sha256"],
              "setup_seconds": perf_counter() - started, "takes": []}
    if args.device == "cuda":
        report["gpu"] = torch.cuda.get_device_name()
        torch.cuda.reset_peak_memory_stats()
    args.output_dir.mkdir(parents=True)
    for index, line in enumerate(lines, 1):
        started = perf_counter()
        wavs, rate = model.generate_voice_clone(text=line, language="Japanese", voice_clone_prompt=prompt)
        elapsed = perf_counter() - started
        path = args.output_dir / f"{index:03d}.wav"
        sf.write(path, wavs[0], rate, subtype="PCM_16")
        duration = len(wavs[0]) / rate
        report["takes"].append({"file": path.name, "text": line, "audio_seconds": duration,
                                "generation_seconds": elapsed})
        if args.device == "cuda":
            report["peak_allocated_mib"] = torch.cuda.max_memory_allocated() / (1024 ** 2)
        (args.output_dir / "timings.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
        )
        print(f"{path} ({duration:.2f}s audio, {elapsed:.2f}s generation)", flush=True)


if __name__ == "__main__":
    main()
