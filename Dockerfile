#########################
# Image for mkabook     #
#########################
FROM alpine as build
RUN echo "---- INSTALL DEPENDENCIES ----" \
    && echo http://dl-cdn.alpinelinux.org/alpine/edge/testing >> /etc/apk/repositories \
    && apk add --no-cache --update --upgrade --virtual=build-dependencies \
    autoconf \
    automake \
    boost-dev \
    build-base \
    gcc \
    git \
    tar \
    fdk-aac-dev \
    wget && \
    echo "---- COMPILE FDKAAC ENCODER (executable binary for usage of --audio-profile) ----" \
    && cd /tmp/ \
    && wget https://github.com/nu774/fdkaac/archive/1.0.0.tar.gz \
    && tar xzf 1.0.0.tar.gz \
    && cd fdkaac-1.0.0 \
    && autoreconf -i && ./configure && make -j4 && make install && rm -rf /tmp/* && \
    echo "---- REMOVE BUILD DEPENDENCIES (to keep image small) ----" \
    && apk del --purge build-dependencies && rm -rf /tmp/*

FROM alpine
ENV WORKDIR /mnt


# Install required applications 
RUN echo "---- INSTALL RUNTIME DEPENDENCIES ----" && \
    apk add --no-cache --update --upgrade \
    python3 \
    mkvtoolnix \
    # fdkaac: required libaries
    && echo http://dl-cdn.alpinelinux.org/alpine/edge/testing >> /etc/apk/repositories \
    && apk add --no-cache --update --upgrade fdk-aac-dev

ADD ./mkabook.py /usr/local/bin/mkabook

# copy ffmpeg static with libfdk from mwader docker image
COPY --from=mwader/static-ffmpeg:4.1.3-1 /ffmpeg /usr/local/bin/
# copy libfdk
COPY --from=build /usr/local/bin/fdkaac /usr/local/bin

WORKDIR ${WORKDIR}
ENTRYPOINT [ "mkabook" ]