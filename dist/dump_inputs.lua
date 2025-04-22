local file = io.open("dump_inputs.txt", "w")

for port_name, port in pairs(manager.machine.ioport.ports) do
    file:write(string.format("PORT:%s\n", port_name))
    for _, field in pairs(port.fields) do
        file:write(string.format("FIELD:%s|%s|%s|%d\n",
            field.name, field.type, port_name, field.mask))
    end
end

file:close()
manager.machine:exit()
