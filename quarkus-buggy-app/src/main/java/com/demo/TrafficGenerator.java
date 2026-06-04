package com.demo;

import io.quarkus.scheduler.Scheduled;
import jakarta.enterprise.context.ApplicationScoped;
import org.jboss.logging.Logger;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;

/**
 * Generates continuous background traffic against the local endpoints so that
 * errors accumulate in Prometheus immediately after deployment.
 */
@ApplicationScoped
public class TrafficGenerator {

    private static final Logger LOG = Logger.getLogger(TrafficGenerator.class);

    private final HttpClient http = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(5))
            .build();

    @Scheduled(every = "5s", delayed = "10s")
    void generateTraffic() {
        String base = "http://localhost:8080";
        callEndpoint(base + "/api/products");
        callEndpoint(base + "/api/orders");
        callEndpoint(base + "/api/inventory");
    }

    private void callEndpoint(String url) {
        try {
            HttpRequest req = HttpRequest.newBuilder()
                    .uri(URI.create(url))
                    .timeout(Duration.ofSeconds(6))
                    .GET()
                    .build();
            HttpResponse<Void> resp = http.send(req, HttpResponse.BodyHandlers.discarding());
            LOG.debugf("Traffic gen → %s → HTTP %d", url, resp.statusCode());
        } catch (Exception e) {
            LOG.warnf("Traffic gen → %s threw: %s", url, e.getMessage());
        }
    }
}
