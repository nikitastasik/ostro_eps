#!/bin/sh
# Пересборка данных региона из открытых источников (в репозитории уже лежит готовый результат в data/sochi).
# Нужны: osmium-tool, python3 с пакетами osmium tifffile imagecodecs h5py numpy shapely pyyaml snowballstemmer.
# Время: ~40 минут (поток планетарного дампа OSM 95 ГБ без сохранения на диск) + ~1 минута сборки.
set -eu
WORK=${WORK:-/tmp/ostrovok-data}
mkdir -p "$WORK/dem" "$WORK/climate/oisst"
PLANET=${PLANET:-https://osm-pds.s3.amazonaws.com/2026/planet-261019.osm.pbf}

echo "1/4 OpenStreetMap: вырезаем Большой Сочи из планетарного дампа (AWS Open Data, без Overpass)"
python3 scripts/stream_s3.py "$PLANET" | osmium extract -b 39.0,43.35,40.45,44.15 --strategy simple -F pbf - -o "$WORK/sochi.osm.pbf" --overwrite

echo "2/4 Рельеф: Copernicus DEM GLO-30"
for t in N43_00_E039_00 N43_00_E040_00 N44_00_E039_00 N44_00_E040_00; do
  curl -sf -o "$WORK/dem/cop_$t.tif" "https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_${t}_DEM/Copernicus_DSM_COG_10_${t}_DEM.tif"
done

echo "3/4 Климат: NOAA GHCN-Daily (станция Сочи) и спутниковая температура моря NOAA OISST"
curl -sf -o "$WORK/climate/RSM00037099.csv" "https://noaa-ghcn-pds.s3.amazonaws.com/csv/by_station/RSM00037099.csv"
for y in 2015 2016 2017 2018 2019 2020 2021 2022 2023 2024 2025; do
  for m in 01 02 03 04 05 06 07 08 09 10 11 12; do
    for d in 05 15 25; do
      echo "https://noaa-cdr-sea-surface-temp-optimum-interpolation-pds.s3.amazonaws.com/data/v2.1/avhrr/$y$m/oisst-avhrr-v02r01.$y$m$d.nc"
    done
  done
done | (cd "$WORK/climate/oisst" && xargs -P 8 -n 1 curl -sfO)
python3 scripts/build_climate.py --ghcn "$WORK/climate/RSM00037099.csv" --oisst-dir "$WORK/climate/oisst" --out data/sochi/climate.json

echo "4/4 Сборка компактного набора данных региона"
python3 scripts/build_region.py --pbf "$WORK/sochi.osm.pbf" --dem-dir "$WORK/dem" --out data/sochi --hotels-bbox 39.665,43.545,39.800,43.660
echo "Готово. Чтобы заново прогнать агентов по всем отелям: python scripts/precompute.py"
