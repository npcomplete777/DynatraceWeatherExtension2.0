from pathlib import Path
from setuptools import setup, find_packages

def find_version() -> str:
    version = "1.0.0"
    extension_yaml_path = Path(__file__).parent / "extension" / "extension.yaml"
    try:
        with open(extension_yaml_path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("version"):
                    version = line.split(" ")[-1].strip("\"")
                    break
    except Exception:
        pass
    return version

setup(
    name="united_weather_ext",
    version=find_version(),
    description="United Weather Extension for Dynatrace",
    author="AHEAD",
    packages=find_packages(),
    python_requires=">=3.10",
    include_package_data=True,
    install_requires=[
        "dt-extensions-sdk==1.4.0",
        "requests>=2.0.0",
        "aiohttp>=3.8.0"
    ],
    extras_require={
        "dev": ["dt-extensions-sdk[cli]==1.4.0"]
    }
)
