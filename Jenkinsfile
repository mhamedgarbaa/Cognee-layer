pipeline {
    agent any
    environment {
        SERVICE_NAME            = "fastapi-template-app"
        BS_CONNECTION_STRING    = "bs-connection-string"
    }
    stages {
        stage('Restart service') {
            steps {
                 script{
                    sh "docker stop ${SERVICE_NAME}"
                    sh "docker rm ${SERVICE_NAME}"
                    sh "docker-compose up -d --force-recreate --build --no-deps ${SERVICE_NAME}"
                 }
            }
        }
    }
}
