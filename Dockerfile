# Use the Miniconda3 image from continuumio as the base image
FROM continuumio/miniconda3

# Set environment variables
ENV PATH /opt/conda/bin:$PATH
ENV PROMETHEUS_MULTIPROC_DIR /workspace/metrics

# Create the /workspace directory and set it as the working directory
WORKDIR /workspace

# Copy all files from the current directory on the host to the container's /workspace directory
COPY . /workspace

# Ensure the correct Python version is installed (Python > 3.12.4) via conda
# Install pip using conda, in case it doesn't come pre-installed
# Update pip to the latest version
# Install dependencies from the requirements.txt file
RUN conda install python=3.12 pip && \
    pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Use JINA for browser now - should be faster and more robust
#RUN playwright install-deps
#RUN playwright install

# Make sure bash is used when connecting to the container
CMD ["/bin/bash"]