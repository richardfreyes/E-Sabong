FROM golang:1.25-alpine AS build
WORKDIR /src
COPY main.go .
RUN go mod init whepload && go get github.com/pion/webrtc/v4@v4.1.6 && go mod tidy && CGO_ENABLED=0 go build -o /whep-load .
FROM alpine:3.22
COPY --from=build /whep-load /usr/local/bin/whep-load
ENTRYPOINT ["whep-load"]
