#!/bin/bash -e

fatal() {
    echo "fatal: $@"
    exit 1
}

[ -d patched ] && fatal "patched directory exists"
mkdir -p patched
cd patched

# bugfix: circular depends (and wrong depends) on fdisk, raid, lvm
tar -zxf ../modules/fdisk.wbm.gz
tar -zxf ../modules/lvm.wbm.gz
sed -i "s|raid ||" fdisk/module.info
sed -i "s|fdisk|fdisk raid|" lvm/module.info
tar -zcf fdisk.wbm.gz fdisk/
tar -zcf lvm.wbm.gz lvm/
rm -rf fdisk/ lvm/

echo "patched modules in patched/"
ls *.wbm.gz

