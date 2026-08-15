#!/usr/bin/env bash
# Spawn a ring of photo-textured obstacle cubes around the takeoff point, so the
# drone's camera sees one whatever its heading. Run while the sim is up.
#
# IMPORTANT: launch the sim with our models on GAZEBO_MODEL_PATH so the texture
# resolves, e.g.:
#   export GAZEBO_MODEL_PATH="$HOME/veriswarm/sim/gazebo/models:$GAZEBO_MODEL_PATH"
#   make px4_sitl gazebo-classic_typhoon_h480
set -u
SDF="$HOME/veriswarm/sim/gazebo/models/vs_obstacle/model.sdf"
if ! command -v gz >/dev/null 2>&1; then
  echo "gz (gazebo classic CLI) not found"; exit 1
fi
i=0
for xy in "10 0" "-10 0" "0 10" "0 -10" "8 8" "-8 8" "8 -8" "-8 -8"; do
  set -- $xy
  if gz model --spawn-file="$SDF" --model-name="vs_obstacle_$i" -x "$1" -y "$2" -z 1.5 >/dev/null 2>&1; then
    echo "  spawned vs_obstacle_$i at ($1, $2)"
  fi
  i=$((i + 1))
done
echo "done ($i obstacles)"
