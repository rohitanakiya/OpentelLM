from setuptools import setup, find_packages

setup(
    name="opentelm",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "httpx>=0.27.0",
    ],
    extras_require={
        "openai": ["openai>=1.0.0"],
        "anthropic": ["anthropic>=0.25.0"],
    },
    python_requires=">=3.10",
)
