FROM continuumio/miniconda3

# TODO: Full r-env installation
# To build the real worker image, first export the conda environment from the host:
#   conda env export -n r-env > worker/r-env.yml
# Then replace the line below with:
#   COPY r-env.yml /tmp/r-env.yml
#   RUN conda env create -f /tmp/r-env.yml
RUN conda install -y r-base=4.3

WORKDIR /workspace
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
