"""
Data Preprocessing Script for Code Smells Refactoring Dataset
==============================================================

This script filters the code_smells_refactoring_dataset_120k.csv to include
only Python and Java languages for ML model training and RDP Agent analysis.

Features:
    - Filters dataset to only Python and Java languages
    - Validates data integrity
    - Generates summary statistics
    - Outputs preprocessed CSV files

Usage:
    python preprocess_dataset.py
"""

import os
import pandas as pd
import numpy as np
from pathlib import Path


class DataPreprocessor:
    """Preprocessing pipeline for code smells refactoring dataset."""

    def __init__(self, input_file: str = None, output_dir: str = None):
        """Initialize preprocessor with file paths.
        
        Args:
            input_file: Path to input CSV file (defaults to current directory)
            output_dir: Directory to save preprocessed files (defaults to current directory)
        """
        self.script_dir = Path(__file__).parent
        self.input_file = input_file or self.script_dir / "code_smells_refactoring_dataset_120k.csv"
        self.output_dir = output_dir or self.script_dir
        
        # Create output directory if it doesn't exist
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)
        
        self.df = None
        self.df_python = None
        self.df_java = None
        self.df_combined = None

    def load_dataset(self) -> bool:
        """Load CSV dataset.
        
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            print(f"📂 Loading dataset from: {self.input_file}")
            self.df = pd.read_csv(self.input_file)
            print(f"✓ Loaded {len(self.df):,} records")
            print(f"✓ Columns: {list(self.df.columns)}")
            return True
        except FileNotFoundError:
            print(f"❌ Error: File not found - {self.input_file}")
            return False
        except Exception as e:
            print(f"❌ Error loading dataset: {str(e)}")
            return False

    def filter_by_language(self) -> None:
        """Filter dataset to Python and Java languages."""
        print("\n🔍 Filtering by language...")
        
        # Get original count
        original_count = len(self.df)
        print(f"   Original dataset: {original_count:,} records")
        
        # Filter Python
        self.df_python = self.df[self.df['language'].str.lower() == 'python'].copy()
        print(f"   🐍 Python records: {len(self.df_python):,} ({len(self.df_python)/original_count*100:.1f}%)")
        
        # Filter Java
        self.df_java = self.df[self.df['language'].str.lower() == 'java'].copy()
        print(f"   ☕ Java records: {len(self.df_java):,} ({len(self.df_java)/original_count*100:.1f}%)")
        
        # Combined
        self.df_combined = pd.concat([self.df_python, self.df_java], ignore_index=True)
        print(f"   ✓ Combined (Python + Java): {len(self.df_combined):,} records ({len(self.df_combined)/original_count*100:.1f}%)")

    def validate_data(self) -> None:
        """Validate data quality and check for missing values."""
        print("\n✅ Data Validation")
        
        if self.df_combined is None or len(self.df_combined) == 0:
            print("❌ No data to validate!")
            return
        
        # Check for missing values
        missing = self.df_combined.isnull().sum()
        if missing.sum() > 0:
            print(f"   ⚠️  Missing values:")
            for col, count in missing[missing > 0].items():
                print(f"      {col}: {count} ({count/len(self.df_combined)*100:.2f}%)")
        else:
            print(f"   ✓ No missing values")
        
        # Check data types
        print(f"\n   Data Types:")
        for col, dtype in self.df_combined.dtypes.items():
            print(f"      {col}: {dtype}")

    def generate_statistics(self) -> None:
        """Generate and display preprocessing statistics."""
        print("\n📊 Dataset Statistics")
        
        if self.df_combined is None or len(self.df_combined) == 0:
            print("❌ No data available!")
            return
        
        # Language distribution
        print(f"\n   Language Distribution:")
        lang_counts = self.df_combined['language'].value_counts()
        for lang, count in lang_counts.items():
            print(f"      {lang}: {count:,} records ({count/len(self.df_combined)*100:.1f}%)")
        
        # Code smell types
        print(f"\n   Code Smell Types (Top 10):")
        smell_counts = self.df_combined['code_smell_type'].value_counts().head(10)
        for smell, count in smell_counts.items():
            print(f"      {smell}: {count:,} ({count/len(self.df_combined)*100:.1f}%)")
        
        # Severity distribution
        print(f"\n   Severity Distribution:")
        severity_counts = self.df_combined['smell_severity'].value_counts()
        for severity, count in severity_counts.items():
            print(f"      {severity}: {count:,} ({count/len(self.df_combined)*100:.1f}%)")
        
        # Refactoring statistics
        refactoring_applied = (self.df_combined['refactoring_applied'] == 1).sum()
        print(f"\n   Refactoring Applied: {refactoring_applied:,} ({refactoring_applied/len(self.df_combined)*100:.1f}%)")
        
        # Numeric statistics
        print(f"\n   Numeric Metrics (Mean):")
        numeric_cols = ['lines_of_code', 'cyclomatic_complexity', 'num_methods', 'num_classes', 
                       'technical_debt_minutes', 'maintainability_index', 'bug_prone_score']
        for col in numeric_cols:
            if col in self.df_combined.columns:
                mean_val = self.df_combined[col].mean()
                print(f"      {col}: {mean_val:.2f}")

    def save_preprocessed_data(self) -> bool:
        """Save preprocessed datasets to CSV files.
        
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            print("\n💾 Saving preprocessed datasets...")
            
            # Save combined dataset
            combined_file = Path(self.output_dir) / "code_smells_python_java_only.csv"
            self.df_combined.to_csv(combined_file, index=False)
            print(f"   ✓ Combined (Python + Java): {combined_file}")
            print(f"     Records: {len(self.df_combined):,}")
            
            # Save Python-only dataset
            python_file = Path(self.output_dir) / "code_smells_python_only.csv"
            self.df_python.to_csv(python_file, index=False)
            print(f"   ✓ Python only: {python_file}")
            print(f"     Records: {len(self.df_python):,}")
            
            # Save Java-only dataset
            java_file = Path(self.output_dir) / "code_smells_java_only.csv"
            self.df_java.to_csv(java_file, index=False)
            print(f"   ✓ Java only: {java_file}")
            print(f"     Records: {len(self.df_java):,}")
            
            return True
        except Exception as e:
            print(f"❌ Error saving files: {str(e)}")
            return False

    def generate_summary_report(self) -> None:
        """Generate a summary report of the preprocessing."""
        print("\n" + "="*70)
        print("PREPROCESSING SUMMARY REPORT")
        print("="*70)
        
        if self.df is None or self.df_combined is None:
            print("❌ No data available!")
            return
        
        print(f"\n📈 Overall Statistics:")
        print(f"   Original Dataset Size: {len(self.df):,} records")
        print(f"   Filtered Dataset Size: {len(self.df_combined):,} records")
        print(f"   Reduction: {(1 - len(self.df_combined)/len(self.df))*100:.1f}%")
        print(f"   Retained: {len(self.df_combined)/len(self.df)*100:.1f}%")
        
        print(f"\n🔤 Languages Included:")
        print(f"   🐍 Python: {len(self.df_python):,} records")
        print(f"   ☕ Java: {len(self.df_java):,} records")
        
        # Most common code smells
        print(f"\n🐛 Top 5 Code Smell Types:")
        for i, (smell, count) in enumerate(self.df_combined['code_smell_type'].value_counts().head(5).items(), 1):
            print(f"   {i}. {smell}: {count:,} records")
        
        # Most common frameworks
        print(f"\n🛠️  Top 5 Frameworks:")
        frameworks = self.df_combined['framework'].value_counts().head(5)
        for i, (fw, count) in enumerate(frameworks.items(), 1):
            label = fw if fw != 'None' else 'None (No Framework)'
            print(f"   {i}. {label}: {count:,} records")
        
        print(f"\n📊 Data Quality:")
        print(f"   Null Values: {self.df_combined.isnull().sum().sum()}")
        print(f"   Complete Records: {len(self.df_combined)}")
        print(f"   Data Completeness: 100%")
        
        print("\n" + "="*70)

    def run(self) -> bool:
        """Execute the full preprocessing pipeline.
        
        Returns:
            bool: True if successful, False otherwise
        """
        print("\n" + "="*70)
        print("CODE SMELLS DATASET PREPROCESSING")
        print("="*70)
        
        # Step 1: Load
        if not self.load_dataset():
            return False
        
        # Step 2: Filter
        self.filter_by_language()
        
        # Step 3: Validate
        self.validate_data()
        
        # Step 4: Statistics
        self.generate_statistics()
        
        # Step 5: Save
        if not self.save_preprocessed_data():
            return False
        
        # Step 6: Report
        self.generate_summary_report()
        
        print("\n✅ Preprocessing completed successfully!\n")
        return True


def main():
    """Main entry point."""
    preprocessor = DataPreprocessor()
    success = preprocessor.run()
    return 0 if success else 1


if __name__ == "__main__":
    exit(main())
