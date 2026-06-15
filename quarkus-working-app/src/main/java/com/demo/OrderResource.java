package com.demo;

import jakarta.ws.rs.GET;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.Produces;
import jakarta.ws.rs.core.MediaType;
import jakarta.ws.rs.core.Response;
import org.jboss.logging.Logger;

import java.util.List;
import java.util.Map;

@Path("/api/orders")
public class OrderResource {

    private static final Logger LOG = Logger.getLogger(OrderResource.class);

    private static final List<Map<String, Object>> ORDERS = List.of(
            Map.of("id", "ORD-001", "product", "Widget A", "qty", 3, "status", "shipped"),
            Map.of("id", "ORD-002", "product", "Gadget B", "qty", 1, "status", "pending"),
            Map.of("id", "ORD-003", "product", "Doohickey C", "qty", 10, "status", "processing")
    );

    @GET
    @Produces(MediaType.APPLICATION_JSON)
    public Response getOrders() {
        LOG.infof("GET /api/orders — returning %d orders", ORDERS.size());
        return Response.ok(ORDERS).build();
    }
}
