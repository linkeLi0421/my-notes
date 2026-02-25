---
title: "LoRA Fine-Tuning Complete Guide"
date: 2026-02-24
project: llm-fine-tuning
topic: complete-guide
---
# LoRA Fine-Tuning Complete Guide

> Learned from fine-tuning Qwen3-0.6B with ruozhiba dataset using LLaMA-Factory.

---

## Table of Contents

1. [LoRA Algorithm](#1-lora-algorithm)
2. [QLoRA (Quantized LoRA)](#2-qlora-quantized-lora)
3. [Training Parameters](#3-training-parameters)
4. [Batch Size, Gradient Accumulation &amp; Cutoff Length](#4-batch-size-gradient-accumulation--cutoff-length)
5. [Output &amp; Logging Parameters](#5-output--logging-parameters)
6. [Training Loss vs Eval Loss Curves](#6-training-loss-vs-eval-loss-curves)
7. [Parameter Update Calculations](#7-parameter-update-calculations)
8. [Experiment Results](#8-experiment-results)

---

## 1. LoRA Algorithm

### The Problem

A model like Qwen3-0.6B has 600 million parameters. Full fine-tuning means updating all 600M parameters - expensive in GPU memory, storage, and time.

### The Core Idea

LoRA's insight: you don't need to change all parameters. The changes during fine-tuning can be represented by much smaller matrices.

### How It Works

Original weight matrix W (e.g., 1024 x 1024 = 1,048,576 params):

```
Input → [W] → Output
```

With LoRA, freeze W and add two small matrices A and B:

```
Input → [W (frozen)] + [A × B (trainable)] → Output
```

Where:

- A is 1024 x r (r = rank, e.g., 8)
- B is r x 1024

Trainable params: 1024×8 + 8×1024 = 16,384 (vs 1,048,576)

That's 98.4% fewer parameters!

### Key Parameters

#### rank (r) - The bottleneck dimension

```
r=4  → Very compressed, fast, may underfit
r=8  → Standard, good balance
r=16 → More capacity
r=32 → High capacity
r=64 → Near full fine-tuning quality
```

Experiment results on Qwen3-0.6B with ruozhiba dataset:

- rank=8:  Train Loss 1.967
- rank=16: Train Loss 1.905, Eval Loss 1.866
- rank=32: Train Loss 1.827, Eval Loss 1.854 (BEST)

#### alpha - Scaling factor

```
Actual update = (alpha / rank) × A × B
```

Rule of thumb: alpha = 2 × rank

Examples:

- alpha=16, rank=8 → scale = 2.0
- alpha=64, rank=32 → scale = 2.0

#### dropout - Regularization

```
dropout=0.0  → No regularization
dropout=0.05 → Light (standard)
dropout=0.1  → Heavy (can hurt performance)
```

Experiment showed dropout=0.1 performed WORST (Eval Loss 1.883 vs 1.854 for best).

#### target modules - Which layers to adapt

lora_target=all applies LoRA to:

- q_proj (query) - Attention
- k_proj (key) - Attention
- v_proj (value) - Attention
- o_proj (output) - Attention
- gate_proj - MLP
- up_proj - MLP
- down_proj - MLP

### Comparison: Full Fine-Tuning vs LoRA

| Aspect            | Full Fine-Tuning | LoRA           |
| ----------------- | ---------------- | -------------- |
| Params trained    | 600M (100%)      | ~10-40M (2-7%) |
| GPU memory        | High             | Low            |
| Storage per model | ~1.2 GB          | ~20 MB adapter |
| Quality           | Best             | Very close     |
| Speed             | Slow             | Fast           |
| Multiple tasks    | Save full model  | Swap adapters  |

### LoRA Variants

- **QLoRA**: LoRA + 4-bit quantization (saves even more memory)
- **DoRA**: Weight-decomposed LoRA (newer, sometimes better)
- **rsLoRA**: Rank-stabilized LoRA (scales better with high rank)
- **PiSSA**: Principal Singular values and Singular vectors Adaptation

### Practical Notes

- LoRA's biggest advantage: keep one base model and swap tiny adapter files for different tasks
- 4 adapters ≈ 80 MB total vs ~5 GB for 4 full fine-tuned models
- Always use validation data to detect overfitting
- Best checkpoint is often NOT the final one - use early stopping

---

## 2. QLoRA (Quantized LoRA)

### The Problem LoRA Didn't Solve

LoRA only trains small adapter matrices (~20MB), but the frozen base model still sits in GPU memory at full precision. For large models this is still too much:

- Qwen3-7B in bf16: ~14 GB GPU memory
- Qwen3-72B in bf16: ~144 GB GPU memory

### QLoRA's Solution

Quantize the frozen base model to 4-bit, then apply LoRA adapters on top at full precision.

```
LoRA:   Base model (bf16, 16-bit) + LoRA adapters (small)
QLoRA:  Base model (4-bit)        + LoRA adapters (bf16, full precision)
```

The base model is 4x smaller in memory. Only the LoRA adapters (which are being trained) stay at full precision.

### Memory Comparison

| Model Size | Full Fine-Tune | LoRA (bf16) | QLoRA (4-bit) |
| ---------- | -------------- | ----------- | ------------- |
| 0.6B       | ~2.4 GB        | ~1.2 GB     | ~0.4 GB       |
| 7B         | ~28 GB         | ~14 GB      | ~4 GB         |
| 14B        | ~56 GB         | ~28 GB      | ~8 GB         |
| 72B        | ~288 GB        | ~144 GB     | ~36 GB        |

QLoRA makes 7B models trainable on a single 8GB consumer GPU.

### How 4-bit Quantization Works

Normal bf16: Each weight stored as 16-bit float (65,536 possible values)
4-bit NF4: Each weight mapped to nearest of 16 values (2^4 = 16)

Information is lost, but frozen weights don't need full precision. Only the LoRA adapters need full precision for gradient computation.

### QLoRA Key Techniques

#### 1. NF4 (NormalFloat 4-bit)

Quantization format optimized for neural network weight distributions (normally distributed). Less information loss than naive 4-bit.

```yaml
quantization_type: nf4
```

#### 2. Double Quantization

Quantizes the quantization constants themselves. Saves extra ~0.4 bits per parameter.

```yaml
double_quantization: true
```

#### 3. Paged Optimizers

Uses CPU RAM as overflow when GPU runs out. Prevents OOM on long sequences.

### How to Use QLoRA in LLaMA-Factory

Add these lines to your training config:

```yaml
quantization_bit: 4              # 4-bit quantization
quantization_method: bnb         # Use bitsandbytes library
quantization_type: nf4           # NormalFloat4 (best)
double_quantization: true        # Quantize the quantization constants
```

Everything else (lora_rank, learning_rate, epochs, etc.) stays the same as regular LoRA.

### LoRA vs QLoRA Comparison

| Aspect               | LoRA           | QLoRA               |
| -------------------- | -------------- | ------------------- |
| Base model precision | bf16 (16-bit)  | 4-bit               |
| GPU memory           | Higher         | ~4x less            |
| Training speed       | Faster         | Slower (dequantize) |
| Quality              | Best           | ~97-99% of LoRA     |
| When to use          | GPU has enough | GPU too small       |

### When You Need QLoRA

- 0.6B model: LoRA is fine, model is tiny
- 7B model on 8GB GPU: NEED QLoRA
- 14B model on 24GB GPU: NEED QLoRA
- 72B model: NEED QLoRA + multi-GPU

### Quality Impact

- Full fine-tune: 100% quality (reference)
- LoRA: ~99% quality
- QLoRA: ~97-99% quality

The quality difference is usually negligible in practice.

---

## 3. Training Parameters

### Learning Depth Parameters (控制学习深度)

#### lora_rank

The bottleneck dimension of LoRA matrices. Controls model capacity.

- r=8: Standard, good balance (baseline)
- r=16: More capacity
- r=32: High capacity (best in experiments)
- r=64: Near full fine-tuning quality
- Higher rank = more trainable parameters = better fit but more memory

#### learning_rate

How big each update step is. Step size when walking downhill to find the lowest loss.

- 1e-3: Too high, overshoots
- 5e-5: Standard for LoRA fine-tuning
- 1e-5: Conservative, more stable but slower
- 1e-6: Too low, may get stuck
- Rule of thumb for LoRA: 1e-5 to 5e-5

#### lr_scheduler_type

How learning rate changes over training time.

- cosine: Starts high, smoothly decreases like cosine curve (most popular)
- linear: Decreases at constant rate
- constant: Never changes
- Cosine lets model learn fast early, then fine-tune details later

#### warmup_ratio

Gradually increase LR at the start instead of jumping to full speed.

- 0.1 = first 10% of steps are warmup
- Prevents instability from large LR on random/early weights
- Training flow: 0 → peak LR (warmup) → cosine decay → ~0

#### max_samples

Limit how many samples from the dataset to use.

- Useful for quick experiments and debugging
- Example: 1500 means only use 1500 samples even if dataset has more
- Omit to use full dataset

#### num_train_epochs

How many times to pass through the entire dataset.

- epoch=1: See each sample once (underfitting risk)
- epoch=3: Standard (good balance)
- epoch=5: More training (overfitting risk)
- More epochs = more training but risk of overfitting

### Evaluation Parameters (控制测试集)

#### val_size

Split portion of training data for validation.

- val_size: 0.1 means 10% of training data becomes validation set
- Range: 0.0 to 1.0 (float) or integer for exact count
- Used to detect overfitting during training
- If train_loss goes down but eval_loss goes up = overfitting

#### eval_dataset

Specify a separate dataset for evaluation instead of splitting training data.

- Alternative to val_size
- Points to a dataset name defined in dataset_info.json
- Better than val_size because train and eval data are completely separate
- Example: eval_dataset: ruozhiba_test

#### per_device_eval_batch_size

How many samples to evaluate at once per GPU.

- Higher = faster evaluation but more GPU memory
- Typical values: 1, 2, 4, 8
- Does NOT affect training quality, only evaluation speed
- Can be larger than training batch size (no gradients needed)

#### eval_strategy

When to run evaluation.

- steps: Evaluate every N steps (use with eval_steps)
- epoch: Evaluate at end of each epoch
- no: Never evaluate (default)

#### eval_steps

How often to evaluate (when eval_strategy=steps).

- eval_steps: 50 means evaluate every 50 training steps
- Lower = more frequent evaluation = slower training but better monitoring

#### load_best_model_at_end

After training, load the checkpoint with best eval_loss.

- Requires eval_strategy to be set
- Saves the best model, not just the final one
- Critical for preventing overfitting

#### metric_for_best_model

Which metric determines the "best" checkpoint.

- eval_loss: Most common, lower is better
- eval_accuracy: If compute_accuracy is enabled

---

## 4. Batch Size, Gradient Accumulation & Cutoff Length

### cutoff_len (cutoff_length)

Maximum token length for each training sample.

```yaml
cutoff_len: 2048
```

What happens to samples based on their token count:

- Tokens < cutoff_len: Padded with pad tokens to fill
- Tokens = cutoff_len: Used as-is
- Tokens > cutoff_len: TRUNCATED (tail cut off, data lost!)

Choosing the right value:

- 512: Fast, low memory, but long samples get cut
- 1024: Good for short Q&A datasets
- 2048: Safe for most datasets (standard)
- 4096: Long conversations, requires more GPU memory

Trade-off: Higher = more GPU memory per sample but preserves long text. Lower = faster training, less memory, but loses long content.

For short Q&A data like ruozhiba (~30-100 tokens per sample), 2048 is overkill but safe.

### per_device_train_batch_size

How many samples each GPU processes in one forward pass.

```yaml
per_device_train_batch_size: 4
```

Effects of batch size:

- Larger batch: Smoother gradients, faster training, but more GPU memory
- Smaller batch: Less GPU memory, can generalize better (noise acts as regularization), but noisier and slower

If you get OOM (Out of Memory), reduce batch size first, then compensate with gradient_accumulation_steps.

### gradient_accumulation_steps

Simulate a larger batch size without needing more GPU memory.

```yaml
gradient_accumulation_steps: 4
```

How it works:

- Normal: Forward N samples → Backward → UPDATE weights immediately
- With accumulation=4: Forward N samples → save gradient, repeat 4 times → AVERAGE all gradients → UPDATE weights once

This means the model sees 4x more samples before each weight update, but only holds 1 mini-batch in GPU memory at a time.

### Effective Batch Size Formula

```
effective_batch = per_device_batch × grad_accum × num_GPUs
```

Examples from experiments:

- Baseline: 4 × 4 × 2 GPUs = 32 effective batch
- Exp4:     2 × 8 × 2 GPUs = 32 effective batch (same result, less memory!)

Same effective batch size, different memory usage:

- batch=8, accum=1: 8 samples in GPU at once (high memory)
- batch=4, accum=2: 4 samples in GPU at once (medium memory)
- batch=2, accum=4: 2 samples in GPU at once (low memory)
- batch=1, accum=8: 1 sample in GPU at once (minimum memory)

All produce identical training behavior but use different amounts of GPU memory. This is the key trick for training large models on limited hardware.

### How These Three Interact

```
Total training steps = (num_samples / effective_batch) × num_epochs
```

Example with ruozhiba (5,986 samples), effective_batch=32, 3 epochs:

- Steps = (5986 / 32) × 3 ≈ 141 steps (matches actual training!)

If cutoff_len is too high and samples are long:

- Each sample uses more GPU memory
- May need to reduce batch_size
- Compensate with higher gradient_accumulation_steps

### Practical Guidelines

#### cutoff_len

- Check your dataset: what's the max token length?
- Set cutoff_len slightly above the 95th percentile
- Don't set unnecessarily high (wastes memory on padding)

#### batch_size

- Start with 4, increase if GPU memory allows
- Reduce to 2 or 1 if OOM errors occur
- Typical range: 1-8 for LoRA fine-tuning

#### gradient_accumulation

- Use to reach target effective batch size (16-64 is common)
- Higher accumulation = fewer weight updates per epoch
- No extra GPU memory cost, only slightly slower (more forward passes)

---

## 5. Output & Logging Parameters

### output_dir

Where all training outputs are saved. Everything goes into this directory.

```yaml
output_dir: saves/qwen3-0.6b/lora/sft
```

Contents of output_dir after training:

```
saves/qwen3-0.6b/lora/sft/
├── adapter_model.safetensors    ← LoRA weights (final model)
├── adapter_config.json          ← LoRA configuration
├── checkpoint-100/              ← Intermediate checkpoint at step 100
├── checkpoint-141/              ← Checkpoint at step 141
├── trainer_state.json           ← Training logs, metrics, loss history
├── training_loss.png            ← Loss curve visualization
├── train_results.json           ← Final summary metrics
├── tokenizer.json               ← Tokenizer files
├── tokenizer_config.json        ← Tokenizer configuration
└── all_results.json             ← Combined results
```

Best practice: use different output_dir for each experiment to keep results separate.

### overwrite_output_dir

Controls what happens if output_dir already exists from a previous run.

```yaml
overwrite_output_dir: true    # Delete old files, start fresh
overwrite_output_dir: false   # ERROR if directory exists (default, safe)
```

- true: Convenient for re-running experiments, but LOSES previous results
- false: Safe, prevents accidental deletion of good checkpoints
- Default is false for safety

Use true during experimentation, false for important/final training runs.

### logging_steps

How often to print and record training metrics during training.

```yaml
logging_steps: 10    # Log every 10 training steps
```

Each log entry records: loss, learning_rate, grad_norm, epoch, step.

Example output during training:

```
Step 10:  loss=2.677, lr=3.0e-05, grad_norm=2.05
Step 20:  loss=2.337, lr=4.9e-05, grad_norm=1.49
Step 30:  loss=2.118, lr=4.8e-05, grad_norm=1.33
```

Different values and their effects:

- logging_steps: 1   → Every step, very verbose, slightly slower
- logging_steps: 10  → Every 10 steps, good balance (standard)
- logging_steps: 50  → Every 50 steps, less detail
- logging_steps: 100 → Minimal logging, fast but may miss trends

Trade-off: Lower = more detailed loss curve but slightly slower. Higher = less noise and faster but may miss important trends.

### Related Parameters

#### save_steps

How often to save a checkpoint (model weights snapshot).

```yaml
save_steps: 100    # Save checkpoint every 100 steps
```

Creates checkpoint-100/, checkpoint-200/, etc. in output_dir.

#### save_total_limit

Maximum number of checkpoints to keep. Older ones are deleted.

```yaml
save_total_limit: 3    # Keep only 3 most recent checkpoints
```

Prevents disk space from filling up during long training runs.

#### plot_loss

Whether to generate a loss curve PNG image after training.

```yaml
plot_loss: true    # Generates training_loss.png in output_dir
```

---

## 6. Training Loss vs Eval Loss Curves

### Two Types of Loss Charts

When you set `plot_loss: true` and have validation enabled, LLaMA-Factory generates two PNG files:

#### 1. training_loss.png - Training Loss

Tracks loss on training data over all steps.

- Shows how well the model fits the data it has seen
- Y-axis: loss (training), X-axis: training step
- Generated for every training run

#### 2. training_eval_loss.png - Eval Loss

Tracks loss on validation data (held-out portion).

- Shows how well the model generalizes to unseen data
- Y-axis: eval_loss, X-axis: step
- Only generated when eval_strategy is set (steps or epoch)
- Number of data points depends on eval_steps

---

## 7. Parameter Update Calculations

### Formula

```
total_updates (steps) = (num_samples / effective_batch) × num_epochs
effective_batch = per_device_batch × grad_accum × num_GPUs
```

### Experiment Results (Qwen3-0.6B with ruozhiba)

| Experiment      | Samples | Eff. Batch | Epochs | Total Updates |
| --------------- | ------- | ---------- | ------ | ------------- |
| Baseline        | 5,986   | 32         | 3      | 141           |
| Exp1 (r=16)     | 5,387   | 32         | 3      | 129           |
| Exp2 (5 epochs) | 5,387   | 32         | 5      | 215           |
| Exp3 (more reg) | 5,387   | 32         | 5      | 215           |
| Exp4 (r=32)     | 5,387   | 32         | 3      | 129           |

Note: Exp1-4 use 5,387 samples because val_size=0.1 holds out 10% (599 samples) for validation.

### What Happens in Each Update

1 update = 1 weight change = 1 "step"

Each update:

1. Forward: Feed effective_batch samples through model
2. Loss: Calculate how wrong predictions are
3. Backward: Compute gradients (direction to improve)
4. Update: Adjust LoRA weights by (gradient × learning_rate)

### Total Samples Seen

Each sample is seen num_epochs times:

| Experiment | Samples × Epochs | Total samples processed |
| ---------- | ----------------- | ----------------------- |
| Baseline   | 5,986 × 3        | 17,958                  |
| Exp1       | 5,387 × 3        | 16,161                  |
| Exp2       | 5,387 × 5        | 26,935                  |
| Exp3       | 5,387 × 5        | 26,935                  |
| Exp4       | 5,387 × 3        | 16,161                  |

### Key Insight

Exp4 won with only 129 updates and 16,161 samples seen. Exp2/Exp3 used 67% more updates (215) but didn't beat it. Model capacity (LoRA rank) mattered more than training duration.

### Effective Batch Size Trick

Different configs can produce the same effective batch:

- batch=4, accum=4, 2 GPUs = 32 (baseline)
- batch=2, accum=8, 2 GPUs = 32 (exp4, less GPU memory)
- batch=1, accum=16, 2 GPUs = 32 (minimum memory)

All produce identical training behavior but different memory usage.

---

## 8. Experiment Results

### Overview

Conducted fine-tuning experiments on Qwen3-0.6B model using LLaMA-Factory with ruozhiba dataset (5,986 Chinese Q&A pairs). Tested different hyperparameters to optimize model performance.

### Base Configuration

- **Model**: Qwen3-0.6B (600M parameters)
- **Method**: LoRA (Low-Rank Adaptation)
- **Dataset**: ruozhiba (Chinese humor/QA)
- **Format**: Alpaca (instruction → output)
- **Base Loss**: 1.967 (original 3 epochs, rank 8)

### Results

| Experiment | rank | epochs | lr   | dropout | Train Loss | Eval Loss |
| ---------- | ---- | ------ | ---- | ------- | ---------- | --------- |
| Baseline   | 8    | 3      | 5e-5 | 0.05    | 1.967      | N/A       |
| Exp1       | 16   | 3      | 5e-5 | 0.05    | 1.905      | 1.866     |
| Exp2       | 8    | 5      | 5e-5 | 0.05    | 1.885      | 1.859     |
| Exp3       | 8    | 5      | 3e-5 | 0.10    | 1.958      | 1.883     |
| Exp4       | 32   | 3      | 5e-5 | 0.05    | 1.827      | 1.854     |

### Key Findings

- Higher LoRA rank significantly improves performance (r=32 best)
- More epochs help but only to a point
- Early stopping crucial (best models not at final step)
- Too much regularization (dropout 0.1) hurts performance
- Validation data essential for detecting overfitting

### Best Model: Exp4 High Capacity (r=32)

- Eval Loss: 1.854 (5.7% improvement over baseline)
- Perplexity: 6.4
- Location: `saves/qwen3-0.6b/lora/exp4_high_capacity`

### Technical Details

- Framework: LLaMA-Factory
- Batch size: 4 (effective 32 with grad accumulation)
- Learning rate: 5e-5 (cosine with 10% warmup)
- Precision: bfloat16
- Training time: ~90-150s per experiment

### Data Formats

- **Alpaca**: instruction/input/output fields (used in this project)
- **ShareGPT**: messages array with roles (alternative for multi-turn chat)

### Commands

```bash
# Chat with best model
llamafactory-cli chat \
  --model_name_or_path models/Qwen3-0.6B \
  --adapter_name_or_path saves/qwen3-0.6b/lora/exp4_high_capacity \
  --template qwen \
  --finetuning_type lora \
  --no_enable_thinking
```
