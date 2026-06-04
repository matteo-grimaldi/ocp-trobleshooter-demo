package com.demo;

import jakarta.ws.rs.GET;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.Produces;
import jakarta.ws.rs.core.MediaType;
import jakarta.ws.rs.core.Response;
import org.jboss.logging.Logger;

import java.util.List;
import java.util.Map;
import java.util.Random;

@Path("/api/orders")
public class OrderResource {

    private static final Logger LOG = Logger.getLogger(OrderResource.class);
    private static final Random RANDOM = new Random();

    private static final List<Map<String, Object>> ORDERS = List.of(
            Map.of("id", "ORD-001", "product", "Widget A", "qty", 3, "status", "shipped"),
            Map.of("id", "ORD-002", "product", "Gadget B", "qty", 1, "status", "pending"),
            Map.of("id", "ORD-003", "product", "Doohickey C", "qty", 10, "status", "processing")
    );

    @GET
    @Produces(MediaType.APPLICATION_JSON)
    public Response getOrders() throws InterruptedException {
        LOG.info("GET /api/orders called");

        if (RANDOM.nextDouble() < 0.20) {
            // Simulate slow downstream database / legacy system call
            LOG.warn("Slow database query detected - waiting for legacy order system");
            Thread.sleep(3000);
        }

        return Response.ok(ORDERS).build();
    }
}
