import pytest

from runboard_agent.masking import mask_cmdline


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("python a.py --token=abc123", "python a.py --token=***"),
        ("python a.py --token abc123 --epochs 3", "python a.py --token *** --epochs 3"),
        ("python a.py --password=p@ss --cfg x.yaml", "python a.py --password=*** --cfg x.yaml"),
        ("HF_TOKEN=hf_abc python x.py", "HF_TOKEN=*** python x.py"),
        ("python a.py --api-key sk-123", "python a.py --api-key ***"),
        ("python a.py --wandb_api_key 0123", "python a.py --wandb_api_key ***"),
        ("python a.py --secret_key=zzz", "python a.py --secret_key=***"),
    ],
)
def test_masks_secret_values(raw, expected):
    assert mask_cmdline(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "python a.py --tokenizer bert-base",
        "ffmpeg -i in.mp4 --keyframe 3",
        "python train.py --cfg configs/a.yaml --rank 0",
        "/usr/local/bin/ollama serve",
    ],
)
def test_leaves_normal_args(raw):
    assert mask_cmdline(raw) == raw


def test_truncates():
    assert len(mask_cmdline("x " * 500)) == 200
