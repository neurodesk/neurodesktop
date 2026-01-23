# set -e

sudo apt-get install lsb-release
wget https://cvmrepo.web.cern.ch/cvmrepo/apt/cvmfs-release-latest_all.deb

echo "[DEBUG]: adding cfms repo"
sudo dpkg -i cvmfs-release-latest_all.deb
echo "[DEBUG]: apt-get update"
sudo apt-get update --allow-unauthenticated
echo "[DEBUG]: apt-get install cvmfs"
sudo apt-get install cvmfs --allow-unauthenticated

sudo mkdir -p /etc/cvmfs/keys/ardc.edu.au/


echo "-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAwUPEmxDp217SAtZxaBep
Bi2TQcLoh5AJ//HSIz68ypjOGFjwExGlHb95Frhu1SpcH5OASbV+jJ60oEBLi3sD
qA6rGYt9kVi90lWvEjQnhBkPb0uWcp1gNqQAUocybCzHvoiG3fUzAe259CrK09qR
pX8sZhgK3eHlfx4ycyMiIQeg66AHlgVCJ2fKa6fl1vnh6adJEPULmn6vZnevvUke
I6U1VcYTKm5dPMrOlY/fGimKlyWvivzVv1laa5TAR2Dt4CfdQncOz+rkXmWjLjkD
87WMiTgtKybsmMLb2yCGSgLSArlSWhbMA0MaZSzAwE9PJKCCMvTANo5644zc8jBe
NQIDAQAB
-----END PUBLIC KEY-----" | sudo tee /etc/cvmfs/keys/ardc.edu.au/neurodesk.ardc.edu.au.pub


echo "CVMFS_USE_GEOAPI=no" | sudo tee /etc/cvmfs/config.d/neurodesk.ardc.edu.au.conf

echo "CVMFS_SERVER_URL=\"http://cvmfs1.neurodesk.org/cvmfs/@fqrn@;http://cvmfs2.neurodesk.org/cvmfs/@fqrn@;http://cvmfs3.neurodesk.org/cvmfs/@fqrn@;\"" | sudo tee -a /etc/cvmfs/config.d/neurodesk.ardc.edu.au.conf

echo 'CVMFS_KEYS_DIR="/etc/cvmfs/keys/ardc.edu.au/"' | sudo tee -a /etc/cvmfs/config.d/neurodesk.ardc.edu.au.conf

echo "CVMFS_HTTP_PROXY=DIRECT" | sudo tee  /etc/cvmfs/default.local
echo "CVMFS_QUOTA_LIMIT=5000" | sudo tee -a  /etc/cvmfs/default.local

sudo cvmfs_config setup
# sudo cvmfs_config wsl2_start
sudo cvmfs_config chksetup

ls /cvmfs/neurodesk.ardc.edu.au

# sudo cvmfs_config umount

cvmfs_config stat -v neurodesk.ardc.edu.au



## Test if containers are on CVMFS:
wget https://raw.githubusercontent.com/NeuroDesk/neurocommand/main/cvmfs/log.txt
echo "debug logfile:"
cat log.txt

ls /cvmfs/neurodesk.ardc.edu.au

while IFS= read -r LINE
do
    echo "[DEBUG] LINE: $LINE"
    IMAGENAME_BUILDDATE="$(cut -d' ' -f1 <<< ${LINE})"
    echo "IMAGENAME_BUILDDATE: $IMAGENAME_BUILDDATE"

    IMAGENAME="$(cut -d'_' -f1,2 <<< ${IMAGENAME_BUILDDATE})"
    BUILDDATE="$(cut -d'_' -f3 <<< ${IMAGENAME_BUILDDATE})"
    echo "[DEBUG] IMAGENAME: $IMAGENAME"
    echo "[DEBUG] BUILDDATE: $BUILDDATE"

    if [[ -f "/cvmfs/neurodesk.ardc.edu.au/containers/$IMAGENAME_BUILDDATE/commands.txt" ]]
    then
        echo "Container exists on CVMFS."
    else
        echo "+++++++++++++++++++++++++++++++++++++++++++++++++++++++"
        echo "[DEBUG] Container does not exist on CVMFS"
        echo "+++++++++++++++++++++++++++++++++++++++++++++++++++++++"
        exit 2
    fi
done < log.txt


## test if files can be accessed ok:
cp /cvmfs/neurodesk.ardc.edu.au/containers/fsl_6.0.5.1_20221016/fsl_6.0.5.1_20221016.simg/opt/fsl-6.0.5.1/data/standard/LowerCingulum_1mm.nii.gz ~/LowerCingulum_1mm.nii.gz
