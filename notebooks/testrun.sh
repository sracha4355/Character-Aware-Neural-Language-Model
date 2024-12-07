#!/bin/bash
#SBATCH --job-name=nlp_ga_english            # Job name to appear in the SLURM queue
#SBATCH --mail-user=yv04378@umbc.edu        # Email for job notifications (replace with your email)
#SBATCH --mail-type=END,FAIL              # Notify on job completion or failure
#SBATCH --mem=20000                       # Memory allocation in MB (40 GB)
#SBATCH --time=70:00:00                    # Maximum runtime for the job (70 hours)
#SBATCH --gres=gpu:1                     # Request 4 GPU for the job
#SBATCH --constraint=rtx_2080            # Specific hardware constraint
#SBATCH --error=english_batch_error.err           # Standard error log file
#SBATCH --output=english_batch_output.out           # Standard error log file

# Notify user the job has started
echo "SLURM job started at $(date)"

echo "Activating conda environment"
eval "$(conda shell.bash hook)"
conda activate /nfs/ada/ryus/users/yv04378/.conda/envs/673-nlp

python3 character_aware_neural_language_final.py > english_output.txt 

echo "SLURM job completed at $(date)"
echo "Logs saved to batch_output.out (standard output) and batch_error.err (error logs)."


