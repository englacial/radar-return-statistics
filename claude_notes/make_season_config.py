"""Write a single-season copy of config_antarctica.yaml for one-season-at-a-time runs."""
import sys

import yaml

season = sys.argv[1]
out_path = sys.argv[2]

with open("config/config_antarctica.yaml") as f:
    config = yaml.safe_load(f)
config["query"]["collections"] = [season]

with open(out_path, "w") as f:
    yaml.safe_dump(config, f)
print(out_path)
