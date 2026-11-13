from setuptools import setup, find_packages

setup(
    name="ecourts",
    version="0.1.0",
    description="eCourts India Production Scraper and Resilient Persistent Task Pipeline",
    author="eCourts Engineering Team",
    packages=find_packages(include=["ecourts", "ecourts.*", "V1", "V1.*", "V2", "V2.*"]),
    python_requires=">=3.9",
    install_requires=[
        "requests>=2.28.0",
        "beautifulsoup4>=4.11.0",
        "pillow>=9.0.0",
        "opencv-python-headless>=4.5.0",
    ],
    extras_require={
        "ocr": ["easyocr>=1.6.0", "torch>=2.0.0"],
        "api": ["2captcha-python>=1.0.0"],
        "excel": ["openpyxl>=3.1"],
    },
    entry_points={
        "console_scripts": [
            "ecourts=ecourts.cli:main",
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "Operating System :: OS Independent",
    ],
)
