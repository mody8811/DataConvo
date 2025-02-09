from setuptools import setup, find_packages

setup(
    name="bokly",
    version="0.1.0",
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        "pandas",
        "numpy",
        "plotly",
        "flask",
        "altair",
        "statsmodels",
        "scikit-learn",
        "openai",
    ],
    package_data={
        "": ["templates/*.html", "static/*.js", "static/*.css"]
    },
    entry_points={
        "console_scripts": [
            "bokly-run = bokly:run_app"
        ]
    },
) 